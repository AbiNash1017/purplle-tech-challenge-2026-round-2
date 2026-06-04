# PROMPT: Create a pytest configuration and mock environment for a FastAPI application that integrates Motor (AsyncIO MongoDB client) and Upstash Redis. Ensure the mock environment simulates database collections, cursors, client pings, distinct queries, and the custom Pub/Sub broker without requiring live external services.
# CHANGES MADE: Implemented custom MockCollection, MockDatabase, and MockMongoClient. Added support for async iterators, mock cursor chaining (sort, limit, to_list), and critical administrative commands like database ping. Resolved AttributeError issues when calling distinct on collections by adding matching logic. Established clean fixture-level database teardown to prevent test pollution.
import asyncio
import pytest
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

# Import app modules to configure
from app.core.config import settings
from app.core.upstash_redis import upstash_redis
from app.core.pub_sub import pubsub_broker

# Ensure settings are configured for test environment
settings.MONGO_URI = "mongodb://localhost:27017/test_store_intelligence"
settings.UPSTASH_REDIS_REST_URL = ""
settings.UPSTASH_REDIS_REST_TOKEN = ""
settings.EXECUTION_MODE = "SIMULATED"

# ---------------------------------------------------------------------------
# Mock MongoDB Classes
# ---------------------------------------------------------------------------

class MockCursor:
    def __init__(self, data: List[Dict[str, Any]]):
        self._data = list(data)

    def sort(self, key, direction=None):
        sort_key = key
        reverse = False
        if isinstance(key, list):
            sort_key = key[0][0]
            reverse = key[0][1] == -1
        elif isinstance(direction, int):
            reverse = direction == -1
            
        def get_val(item):
            val = item.get(sort_key)
            if val is None:
                return datetime.min if reverse else datetime.max
            return val
            
        try:
            self._data.sort(key=get_val, reverse=reverse)
        except Exception:
            pass
        return self

    def limit(self, count: int):
        self._data = self._data[:count]
        return self

    async def to_list(self, length: int):
        await asyncio.sleep(0)  # yield control
        return self._data[:length]


class MockCollection:
    def __init__(self, name: str, db):
        self.name = name
        self.db = db
        self._data: List[Dict[str, Any]] = []

    def clear(self):
        self._data.clear()

    async def create_index(self, keys, **kwargs):
        return "mock_index"

    async def count_documents(self, filter_dict):
        count = 0
        for doc in self._data:
            if self._matches(doc, filter_dict):
                count += 1
        return count

    async def distinct(self, key, filter_dict=None):
        matched_values = set()
        for doc in self._data:
            if filter_dict is None or self._matches(doc, filter_dict):
                val = doc.get(key)
                if val is not None:
                    matched_values.add(val)
        return list(matched_values)

    async def insert_one(self, doc):
        from copy import deepcopy
        doc_copy = deepcopy(doc)
        if "_id" not in doc_copy:
            doc_copy["_id"] = f"{self.name}_{len(self._data) + 1}"
        self._data.append(doc_copy)
        return doc_copy

    async def insert_many(self, docs):
        from copy import deepcopy
        added = []
        for doc in docs:
            doc_copy = deepcopy(doc)
            if "_id" not in doc_copy:
                doc_copy["_id"] = f"{self.name}_{len(self._data) + len(added) + 1}"
            self._data.append(doc_copy)
            added.append(doc_copy)
        return added

    async def update_one(self, filter_dict, update_dict, upsert=False):
        target = None
        for doc in self._data:
            if self._matches(doc, filter_dict):
                target = doc
                break

        if target is None:
            if upsert:
                new_doc = {}
                for k, v in filter_dict.items():
                    if not k.startswith("$") and "." not in k:
                        new_doc[k] = v
                if "$setOnInsert" in update_dict:
                    new_doc.update(update_dict["$setOnInsert"])
                if "$set" in update_dict:
                    new_doc.update(update_dict["$set"])
                if "$inc" in update_dict:
                    for k, val in update_dict["$inc"].items():
                        new_doc[k] = val
                if "$push" in update_dict:
                    for k, val in update_dict["$push"].items():
                        new_doc[k] = [val]
                new_doc["_id"] = f"{self.name}_{len(self._data) + 1}"
                self._data.append(new_doc)
                return new_doc
            return None

        # Apply update
        if "$set" in update_dict:
            for k, val in update_dict["$set"].items():
                if "." in k:
                    self._set_nested(target, k, val, filter_dict)
                else:
                    target[k] = val
        if "$inc" in update_dict:
            for k, val in update_dict["$inc"].items():
                target[k] = target.get(k, 0) + val
        if "$push" in update_dict:
            for k, val in update_dict["$push"].items():
                if k not in target or target[k] is None:
                    target[k] = []
                target[k].append(val)
        return target

    def _set_nested(self, doc, path, val, filter_dict):
        parts = path.split(".")
        if len(parts) == 2:
            parent, child = parts
            if parent not in doc or not isinstance(doc[parent], dict):
                doc[parent] = {}
            doc[parent][child] = val
        elif parts[0] == "visit_segments" and parts[1] == "$" and parts[2] == "exited_at":
            for seg in doc.get("visit_segments", []):
                if seg.get("exited_at") is None:
                    seg["exited_at"] = val
                    break

    async def find_one(self, filter_dict, sort=None):
        matched = []
        for doc in self._data:
            if self._matches(doc, filter_dict):
                matched.append(doc)
        if not matched:
            return None
        cursor = MockCursor(matched)
        if sort:
            cursor.sort(sort)
        res = await cursor.to_list(1)
        return res[0] if res else None

    def find(self, filter_dict):
        matched = []
        for doc in self._data:
            if self._matches(doc, filter_dict):
                matched.append(doc)
        return MockCursor(matched)

    def aggregate(self, pipeline):
        return MockCursor(self._run_aggregation(pipeline))

    def _matches(self, doc, filter_dict) -> bool:
        for k, v in filter_dict.items():
            if k == "$match":
                return self._matches(doc, v)
            if k == "store_id":
                if doc.get("store_id") != v:
                    return False
            elif k == "id_token" or k == "track_id":
                val = doc.get("id_token") or doc.get("track_id")
                if val != v:
                    return False
            elif k == "order_id":
                if doc.get("order_id") != v:
                    return False
            elif k == "event_type":
                if isinstance(v, dict) and "$in" in v:
                    if doc.get("event_type") not in v["$in"]:
                        return False
                else:
                    if doc.get("event_type") != v:
                        return False
            elif k == "zone_type":
                if isinstance(v, dict) and "$in" in v:
                    if doc.get("zone_type") not in v["$in"]:
                        return False
                else:
                    if doc.get("zone_type") != v:
                        return False
            elif k == "zone_id":
                if isinstance(v, dict):
                    if "$exists" in v and v["$exists"] and "zone_id" not in doc:
                        return False
                    if "$ne" in v and doc.get("zone_id") == v["$ne"]:
                        return False
                else:
                    if doc.get("zone_id") != v:
                        return False
            elif k == "timestamp" or k == "last_seen":
                if isinstance(v, dict):
                    doc_val = doc.get(k)
                    if not doc_val:
                        return False
                    if isinstance(doc_val, str):
                        doc_val = datetime.fromisoformat(doc_val)
                    if "$gte" in v:
                        cutoff = v["$gte"]
                        if isinstance(cutoff, str):
                            cutoff = datetime.fromisoformat(cutoff)
                        if doc_val < cutoff:
                            return False
                    if "$lte" in v:
                        cutoff = v["$lte"]
                        if isinstance(cutoff, str):
                            cutoff = datetime.fromisoformat(cutoff)
                        if doc_val > cutoff:
                            return False
            elif k == "visit_segments":
                if "$elemMatch" in v:
                    match_cond = v["$elemMatch"]
                    any_match = False
                    for seg in doc.get("visit_segments", []):
                        seg_match = True
                        for sk, sv in match_cond.items():
                            if sv is None and seg.get(sk) is not None:
                                seg_match = False
                            elif sv is not None and seg.get(sk) != sv:
                                seg_match = False
                        if seg_match:
                            any_match = True
                            break
                    if not any_match:
                        return False
        return True

    def _run_aggregation(self, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if self.name == "customer_sessions":
            store_id = None
            for stage in pipeline:
                if "$match" in stage and "store_id" in stage["$match"]:
                    store_id = stage["$match"]["store_id"]
            
            matching_sessions = [doc for doc in self._data if doc.get("store_id") == store_id]
            durations = []
            for doc in matching_sessions:
                for seg in doc.get("visit_segments", []):
                    entered = seg.get("entered_at")
                    exited = seg.get("exited_at")
                    if entered and exited:
                        if isinstance(entered, str):
                            entered = datetime.fromisoformat(entered)
                        if isinstance(exited, str):
                            exited = datetime.fromisoformat(exited)
                        delta = (exited - entered).total_seconds()
                        if delta > 0:
                            durations.append(delta)
            
            avg_dwell = sum(durations) / len(durations) if durations else 0.0
            return [{"_id": None, "avg_dwell": avg_dwell}]

        elif self.name == "spatial_events":
            is_queue = False
            is_zone = False
            is_correlation = False
            is_heatmap = False
            store_id = None

            for stage in pipeline:
                if "$match" in stage:
                    m = stage["$match"]
                    if "store_id" in m:
                        store_id = m["store_id"]
                    if "event_type" in m:
                        et = m["event_type"]
                        if isinstance(et, dict) and "$in" in et:
                            if "queue_completed" in et["$in"]:
                                is_queue = True
                            if "zone_entered" in et["$in"]:
                                is_heatmap = True
                        elif et == "zone_exited":
                            is_zone = True
                        elif et == "exit":
                            is_correlation = True
                    if "location" in m:
                        is_heatmap = True

            if is_queue:
                q_events = [
                    e for e in self._data 
                    if e.get("store_id") == store_id and e.get("event_type") in ["queue_completed", "queue_abandoned"]
                ]
                total = len(q_events)
                abandoned = sum(1 for e in q_events if e.get("event_type") == "queue_abandoned")
                wait_times = [float(e.get("wait_seconds", 0)) for e in q_events if e.get("wait_seconds") is not None]
                avg_wait = sum(wait_times) / len(wait_times) if wait_times else 0.0
                return [{"_id": None, "avg_wait": avg_wait, "total_count": total, "abandoned_count": abandoned}]

            elif is_zone:
                zone_exits = [
                    e for e in self._data
                    if e.get("store_id") == store_id and e.get("event_type") == "zone_exited" and e.get("zone_id") is not None
                ]
                counts = {}
                dwells = {}
                for e in zone_exits:
                    zid = e["zone_id"]
                    counts[zid] = counts.get(zid, 0) + 1
                    dwells[zid] = dwells.get(zid, 0.0) + float(e.get("wait_seconds") or 30.0)
                
                results = []
                for zid in counts:
                    results.append({
                        "_id": zid,
                        "total_visits": counts[zid],
                        "total_dwell": dwells[zid]
                    })
                return results

            elif is_correlation:
                exits = [
                    e for e in self._data
                    if e.get("store_id") == store_id and e.get("event_type") == "exit"
                ]
                pos_txs = self.db["pos_transactions"]._data
                correlated_count = 0
                correlated_track_ids = set()
                for ex in exits:
                    ts = ex["timestamp"]
                    if isinstance(ts, str):
                        ts = datetime.fromisoformat(ts)
                    
                    correlated = False
                    for tx in pos_txs:
                        if tx.get("store_id") != store_id:
                            continue
                        txts = tx["timestamp"]
                        if isinstance(txts, str):
                            txts = datetime.fromisoformat(txts)
                        if abs((txts - ts).total_seconds()) <= 60:
                            correlated = True
                            break
                    if correlated:
                        correlated_count += 1
                        tid = ex.get("track_id")
                        if tid:
                            correlated_track_ids.add(tid)
                return [{
                    "_id": None, 
                    "correlated_count": correlated_count,
                    "track_ids": list(correlated_track_ids)
                }]

            elif is_heatmap:
                heatmap_events = [
                    e for e in self._data
                    if e.get("store_id") == store_id and e.get("event_type") in ["zone_entered", "zone_exited", "zone_update"] and e.get("location") is not None
                ]
                coord_weights = {}
                for e in heatmap_events:
                    coords = tuple(e["location"]["coordinates"])
                    weight = float(e.get("wait_seconds") or 10.0)
                    coord_weights[coords] = coord_weights.get(coords, 0.0) + weight
                
                results = []
                for (x, y), intensity in coord_weights.items():
                    results.append({"x": x, "y": y, "intensity": intensity})
                return results

        return []


class MockMongoDatabase:
    def __init__(self):
        self._collections: Dict[str, MockCollection] = {}

    def __getitem__(self, name: str) -> MockCollection:
        if name not in self._collections:
            self._collections[name] = MockCollection(name, self)
        return self._collections[name]

    async def list_collection_names(self):
        return list(self._collections.keys())

    async def create_collection(self, name: str, **kwargs):
        if name not in self._collections:
            self._collections[name] = MockCollection(name, self)
        return self._collections[name]

    async def command(self, cmd_name: str, *args, **kwargs):
        if cmd_name == "ping":
            return {"ok": 1.0}
        return {}


# ---------------------------------------------------------------------------
# Global Pytest Fixtures
# ---------------------------------------------------------------------------

# Single Mock DB instance for the session
_global_mock_db = MockMongoDatabase()

@pytest.fixture(scope="session", autouse=True)
def patch_db_client():
    """Monkeypatch get_db_client globally across all modules before app startup."""
    import app.core.database
    import app.api.endpoints
    import app.services.ingestion
    import app.services.analytics
    import app.services.simulated_data

    mock_func = lambda: _global_mock_db

    app.core.database.get_db_client = mock_func
    app.api.endpoints.get_db_client = mock_func
    app.services.ingestion.get_db_client = mock_func
    app.services.analytics.get_db_client = mock_func
    app.services.simulated_data.get_db_client = mock_func
    
    # Also patch direct class variables in singletons
    from app.services.ingestion import ingestion_service
    from app.services.analytics import analytics_service
    ingestion_service.db = _global_mock_db
    analytics_service.db = _global_mock_db


@pytest.fixture(autouse=True)
def clean_databases():
    """Reset mock data, Redis, and PubSub, and seed zones and POS fresh before each test."""
    # 1. Reset Mongo collections
    for collection in _global_mock_db._collections.values():
        collection.clear()
        
    # 2. Reset Redis fallback
    upstash_redis._fallback_db.clear()
    upstash_redis._warned_config = False
    
    # 3. Reset PubSub channels
    pubsub_broker._channels.clear()

    # 4. Run app's database initialization synchronously to seed zones and POS transactions
    from app.core.database import init_db
    try:
        loop = asyncio.get_running_loop()
        # If already running (inside async context), schedule it
        future = asyncio.run_coroutine_threadsafe(init_db(), loop)
        future.result()
    except RuntimeError:
        # No running loop — create one and run
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(init_db())
        finally:
            loop.close()
            asyncio.set_event_loop(None)


@pytest.fixture
def mock_db():
    """Fixture to access the mock database inside test functions."""
    return _global_mock_db


@pytest.fixture
def anyio_backend():
    """Required by anyio for async tests."""
    return "asyncio"
