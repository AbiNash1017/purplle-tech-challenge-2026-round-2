"""
SimulatedDataService
====================
Reads processed pipeline output (JSONL events + summary.json) from
  store-intelligence/pipeline/data/output/<store_id>/
and derives the same metrics / heatmap structures that the live MongoDB
analytics service produces.

Priority logic (per endpoint):
  1. If a summary.json exists for the requested store_id → serve computed
     metrics from the JSONL files (no DB needed).
  2. Otherwise fall back to the live AnalyticsService (MongoDB pipeline).
"""

import os
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple

from app.models.schemas import MetricsResponse, ZoneHeatmapPoint
from app.core.database import get_db_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
# Allow override via env; default relative to project root.
_DEFAULT_OUTPUT_ROOT = os.path.join(
    os.path.dirname(__file__),          # app/services/
    "..", "..",                          # project root
    "store-intelligence", "pipeline", "data", "output"
)

def _output_root() -> str:
    return os.environ.get("PIPELINE_OUTPUT_DIR", os.path.normpath(_DEFAULT_OUTPUT_ROOT))


# ---------------------------------------------------------------------------
# Availability helpers
# ---------------------------------------------------------------------------

def get_simulated_store_ids() -> List[str]:
    """Return all store IDs that have a processed summary.json available."""
    root = _output_root()
    if not os.path.isdir(root):
        return []
    ids = []
    for name in os.listdir(root):
        summary_path = os.path.join(root, name, "summary.json")
        if os.path.isfile(summary_path):
            ids.append(name)
    return ids


def has_simulated_data(store_id: str) -> bool:
    """True when pipeline output is available for the given store."""
    summary_path = os.path.join(_output_root(), store_id, "summary.json")
    return os.path.isfile(summary_path)


def load_summary(store_id: str) -> Optional[Dict[str, Any]]:
    summary_path = os.path.join(_output_root(), store_id, "summary.json")
    if not os.path.isfile(summary_path):
        return None
    with open(summary_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# JSONL loader
# ---------------------------------------------------------------------------

def _load_events(store_id: str) -> List[Dict[str, Any]]:
    """
    Stream all *_events.jsonl files for a store into a flat list.
    Skips malformed lines silently.
    """
    store_dir = os.path.join(_output_root(), store_id)
    events: List[Dict[str, Any]] = []
    if not os.path.isdir(store_dir):
        return events

    consolidated_file = f"{store_id}_events.jsonl"
    consolidated_path = os.path.join(store_dir, consolidated_file)

    files_to_read = []
    if os.path.isfile(consolidated_path):
        files_to_read = [consolidated_file]
    else:
        for fname in os.listdir(store_dir):
            if fname.endswith("_events.jsonl") and fname != consolidated_file:
                files_to_read.append(fname)

    for fname in files_to_read:
        fpath = os.path.join(store_dir, fname)
        with open(fpath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    # Normalize fields
                    id_token = data.get("id_token")
                    track_id = data.get("track_id")
                    if id_token is not None and track_id is None:
                        data["track_id"] = str(id_token)

                    ts = data.get("timestamp") or data.get("event_timestamp") or data.get("event_time")
                    if ts:
                        data["timestamp"] = ts

                    events.append(data)
                except json.JSONDecodeError:
                    pass
    return events


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------

def _parse_ts(ts_str: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


async def compute_metrics(store_id: str) -> Optional[MetricsResponse]:
    """
    Derive MetricsResponse from the pipeline JSONL output.
    Returns None if no data is available.
    """
    summary = load_summary(store_id)
    if summary is None:
        return None

    events = _load_events(store_id)
    if not events:
        logger.warning(f"[SimulatedData] No events found for store {store_id}")
        return None

    # ---- Separate customer events only (staff excluded from dashboard) ----
    customer_events = [e for e in events if not e.get("is_staff", False)]

    # ---- 1. Footfall: unique customer track IDs ----
    unique_customer_ids = {e["track_id"] for e in customer_events}
    footfall_count = len(unique_customer_ids)

    # ---- 2. Active customers: query live customer sessions from MongoDB ----
    database = get_db_client()
    now = datetime.now(timezone.utc)
    active_cutoff = now - timedelta(minutes=2)
    try:
        active_customers = await database["customer_sessions"].count_documents({
            "store_id": store_id,
            "last_seen": {"$gte": active_cutoff},
            "visit_segments": {
                "$elemMatch": {"exited_at": None}
            }
        })
    except Exception as e:
        logger.error(f"[SimulatedData] Failed to fetch active customers from DB: {e}")
        active_customers = 0

    # ---- 3. Average dwell time ----
    # Pair entry → exit per track and compute duration
    first_entry: Dict[str, datetime] = {}
    last_exit: Dict[str, datetime] = {}
    for e in customer_events:
        et = e.get("event_type", "")
        tid = e.get("track_id", "")
        ts = _parse_ts(e.get("timestamp", ""))
        if ts is None:
            continue
        if et == "entry":
            if tid not in first_entry or ts < first_entry[tid]:
                first_entry[tid] = ts
        elif et == "exit":
            if tid not in last_exit or ts > last_exit[tid]:
                last_exit[tid] = ts

    dwell_times = []
    for tid in first_entry:
        if tid in last_exit:
            delta = (last_exit[tid] - first_entry[tid]).total_seconds()
            if delta > 0:
                dwell_times.append(delta)

    avg_dwell_seconds = (sum(dwell_times) / len(dwell_times)) if dwell_times else 0.0

    # ---- 4. Queue / billing metrics ----
    # Use camera role info from summary to identify billing cam
    billing_cam_ids = {
        cam["cam_id"]
        for cam in summary.get("cameras", [])
        if cam.get("cam_role") == "billing"
    }

    billing_events = [
        e for e in customer_events
        if e.get("camera_id") in billing_cam_ids
    ]

    queue_tracks: Dict[str, datetime] = {}   # track_id → entry time into billing cam
    queue_wait_times: List[float] = []

    for e in billing_events:
        et = e.get("event_type", "")
        tid = e.get("track_id", "")
        ts = _parse_ts(e.get("timestamp", ""))
        if ts is None:
            continue
        if et == "entry":
            queue_tracks[tid] = ts
        elif et == "exit" and tid in queue_tracks:
            wait = (ts - queue_tracks.pop(tid)).total_seconds()
            if wait > 0:
                queue_wait_times.append(wait)

    queue_wait_seconds_avg = (
        sum(queue_wait_times) / len(queue_wait_times)
    ) if queue_wait_times else 0.0

    # Live queue depth = query live billing queue from MongoDB
    billing_cutoff = now - timedelta(minutes=1)
    try:
        queue_depth = await database["spatial_events"].count_documents({
            "store_id": store_id,
            "event_type": "zone_entered",
            "zone_type": "BILLING",
            "timestamp": {"$gte": billing_cutoff}
        })
        if queue_depth == 0:
            snapshot_event = await database["spatial_events"].find_one(
                {"store_id": store_id, "event_type": "queue_completed"},
                sort=[("timestamp", -1)]
            )
            if snapshot_event:
                queue_depth = max(snapshot_event.get("queue_position_at_join", 1) - 1, 0)
    except Exception as e:
        logger.error(f"[SimulatedData] Failed to fetch queue depth from DB: {e}")
        queue_depth = 0

    # Abandon rate heuristic: tracks that entered billing but stayed < 30s
    SHORT_WAIT_THRESHOLD = 30.0
    abandoned_count = sum(1 for w in queue_wait_times if w < SHORT_WAIT_THRESHOLD)
    total_billing_completed = len(queue_wait_times)
    queue_abandon_rate = (
        abandoned_count / total_billing_completed
    ) if total_billing_completed > 0 else 0.0

    # ---- 5. POS correlation rate ----
    # Simulated: approximate based on exit events vs billing completions
    exit_count = sum(1 for e in customer_events if e.get("event_type") == "exit")
    pos_correlation_rate = (
        min(total_billing_completed / exit_count, 1.0)
        if exit_count > 0 else 0.0
    )

    # ---- 6. Zone breakdown from the zones seeded in DB + coordinate mapping ----
    zone_breakdown = _compute_zone_breakdown(store_id, customer_events)

    return MetricsResponse(
        store_id=store_id,
        timestamp=datetime.now(timezone.utc),
        active_customers=active_customers,
        footfall_count=footfall_count,
        avg_dwell_seconds=round(avg_dwell_seconds, 2),
        queue_wait_seconds_avg=round(queue_wait_seconds_avg, 2),
        queue_abandon_rate=round(queue_abandon_rate, 4),
        queue_depth=queue_depth,
        pos_correlation_rate=round(pos_correlation_rate, 4),
        zone_breakdown=zone_breakdown,
    )


# ---------------------------------------------------------------------------
# Zone breakdown
# ---------------------------------------------------------------------------

# Inline zone definitions mirrored from database.py to avoid DB round-trips
# in simulated mode.
_ZONE_DEFS: Dict[str, List[Dict[str, Any]]] = {
    "ST1076": [
        {"zone_id": "Z01", "zone_name": "Left Wall Shelves (Salm/TFS)",           "zone_type": "SHELF",    "bbox": (0.0,  0.0,  0.38, 0.25)},
        {"zone_id": "Z02", "zone_name": "Right Wall Shelves (Minimalis/Aqualogi)", "zone_type": "SHELF",    "bbox": (0.52, 0.0,  1.0,  0.25)},
        {"zone_id": "Z03", "zone_name": "F.O.H Center (Fragrance/Nail)",           "zone_type": "DISPLAY",  "bbox": (0.3,  0.3,  0.55, 0.65)},
        {"zone_id": "Z04", "zone_name": "Makeup Unit Center",                      "zone_type": "DISPLAY",  "bbox": (0.52, 0.3,  0.75, 0.65)},
        {"zone_id": "Z05", "zone_name": "Bottom Wall (Fac/Mars/Mens/Lo'real)",     "zone_type": "SHELF",    "bbox": (0.1,  0.75, 0.95, 1.0)},
        {"zone_id": "Z06", "zone_name": "Billing Counter Queue",                   "zone_type": "BILLING",  "bbox": (0.82, 0.25, 1.0,  0.75)},
        {"zone_id": "Z07", "zone_name": "Entrance Corridor",                       "zone_type": "ENTRANCE", "bbox": (0.0,  0.3,  0.18, 0.7)},
    ],
    "ST1008": [
        {"zone_id": "Z01", "zone_name": "Left Wall Units (Wall Unit 1-6)",         "zone_type": "SHELF",    "bbox": (0.0,  0.35, 0.12, 1.0)},
        {"zone_id": "Z02", "zone_name": "Top Wall Units (Wall Unit 7-13)",         "zone_type": "SHELF",    "bbox": (0.0,  0.35, 1.0,  0.48)},
        {"zone_id": "Z03", "zone_name": "Right Wall Units (Wall Unit 14-19)",      "zone_type": "SHELF",    "bbox": (0.88, 0.35, 1.0,  1.0)},
        {"zone_id": "Z04", "zone_name": "MK-Gondola Center Displays",             "zone_type": "DISPLAY",  "bbox": (0.15, 0.55, 0.45, 0.95)},
        {"zone_id": "Z05", "zone_name": "Makeup Units (Right-Center)",             "zone_type": "DISPLAY",  "bbox": (0.58, 0.6,  0.85, 0.9)},
        {"zone_id": "Z06", "zone_name": "Billing Counter Queue",                   "zone_type": "BILLING",  "bbox": (0.38, 0.42, 0.62, 0.58)},
        {"zone_id": "Z07", "zone_name": "Main Entrance",                           "zone_type": "ENTRANCE", "bbox": (0.3,  0.9,  0.7,  1.0)},
    ],
}


def _point_in_bbox(nx: float, ny: float, bbox: Tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = bbox
    return x0 <= nx <= x1 and y0 <= ny <= y1


def _normalize_coord(val: float, max_dim: float = 1000.0) -> float:
    """Normalise a pixel value to [0, 1]."""
    if val > 1.0:
        return min(val / max_dim, 1.0)
    return val


def _compute_zone_breakdown(store_id: str, customer_events: List[Dict[str, Any]]) -> List[ZoneHeatmapPoint]:
    zone_defs = _ZONE_DEFS.get(store_id, [])

    # Track visit counts and accumulated dwell per zone
    visit_counts: Dict[str, int] = {z["zone_id"]: 0 for z in zone_defs}
    dwell_totals: Dict[str, float] = {z["zone_id"]: 0.0 for z in zone_defs}

    # Per-track, per-zone entry timestamps
    zone_entry_ts: Dict[str, Dict[str, datetime]] = {}  # zone_id → {track_id → entry_ts}
    for z in zone_defs:
        zone_entry_ts[z["zone_id"]] = {}

    for e in customer_events:
        x_raw = e.get("zone_hotspot_x")
        y_raw = e.get("zone_hotspot_y")
        if x_raw is None or y_raw is None:
            continue

        nx = _normalize_coord(float(x_raw))
        ny = _normalize_coord(float(y_raw))
        tid = e.get("track_id", "")
        ts = _parse_ts(e.get("timestamp", ""))
        if ts is None:
            continue

        et = e.get("event_type", "")

        for zone in zone_defs:
            zid = zone["zone_id"]
            bbox = zone["bbox"]
            in_zone = _point_in_bbox(nx, ny, bbox)

            if in_zone:
                if tid not in zone_entry_ts[zid]:
                    # New visit to this zone
                    zone_entry_ts[zid][tid] = ts
                    visit_counts[zid] += 1
            else:
                # Track left zone — record dwell if we had an entry
                if tid in zone_entry_ts[zid]:
                    entry = zone_entry_ts[zid].pop(tid)
                    dwell = (ts - entry).total_seconds()
                    if dwell > 0:
                        dwell_totals[zid] += dwell

    # Flush any still-open zone stays
    for zone in zone_defs:
        zid = zone["zone_id"]
        for tid, entry in zone_entry_ts[zid].items():
            # Assign a default dwell of 30s for open-ended visits
            dwell_totals[zid] += 30.0

    return [
        ZoneHeatmapPoint(
            zone_id=z["zone_id"],
            zone_name=z["zone_name"],
            zone_type=z["zone_type"],
            dwell_seconds=round(dwell_totals[z["zone_id"]], 2),
            visit_count=visit_counts[z["zone_id"]],
        )
        for z in zone_defs
    ]


# ---------------------------------------------------------------------------
# Heatmap
# ---------------------------------------------------------------------------

def compute_heatmap(store_id: str) -> List[Dict[str, Any]]:
    """
    Returns a list of {x, y, intensity} points derived from all
    customer zone_update / zone_entered / zone_exited events.
    """
    events = _load_events(store_id)
    if not events:
        return []

    INCLUDE_TYPES = {"zone_entered", "zone_exited", "zone_update", "entry", "exit"}
    coord_weights: Dict[Tuple[float, float], float] = {}

    for e in events:
        if e.get("is_staff", False):
            continue
        if e.get("event_type") not in INCLUDE_TYPES:
            continue

        x_raw = e.get("zone_hotspot_x")
        y_raw = e.get("zone_hotspot_y")
        if x_raw is None or y_raw is None:
            continue

        # Round to 2-dp buckets to aggregate nearby points
        nx = round(_normalize_coord(float(x_raw)), 2)
        ny = round(_normalize_coord(float(y_raw)), 2)
        weight = float(e.get("wait_seconds", 5))
        key = (nx, ny)
        coord_weights[key] = coord_weights.get(key, 0.0) + weight

    return [
        {"x": x, "y": y, "intensity": intensity}
        for (x, y), intensity in coord_weights.items()
    ]


# ---------------------------------------------------------------------------
# Public data-source status helper
# ---------------------------------------------------------------------------

def get_data_source_info(store_id: str) -> Dict[str, Any]:
    """Returns metadata about which data source is active for a store."""
    if has_simulated_data(store_id):
        summary = load_summary(store_id)
        return {
            "store_id": store_id,
            "source": "simulated",
            "description": "Serving pre-processed pipeline output data",
            "processed_at": summary.get("processed_at") if summary else None,
            "cameras": [
                {
                    "cam_id": c["cam_id"],
                    "cam_role": c["cam_role"],
                    "total_events": c["events"],
                    "customers": c["customers"],
                    "staff": c["staff"],
                    "video_path": f"/videos/{store_id}/{c['cam_id']}_annotated.mp4",
                }
                for c in (summary.get("cameras", []) if summary else [])
            ],
            "totals": summary.get("totals") if summary else {},
        }
    return {
        "store_id": store_id,
        "source": "live_pipeline",
        "description": "Serving live data from MongoDB ingestion pipeline",
        "processed_at": None,
        "cameras": [],
        "totals": {},
    }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class SimulatedDataService:
    """Thin façade to match the AnalyticsService interface."""

    def has_data(self, store_id: str) -> bool:
        return has_simulated_data(store_id)

    async def get_store_metrics(self, store_id: str) -> Optional[MetricsResponse]:
        return await compute_metrics(store_id)

    async def get_zone_heatmap(self, store_id: str) -> List[Dict[str, Any]]:
        return compute_heatmap(store_id)

    def get_data_source_info(self, store_id: str) -> Dict[str, Any]:
        return get_data_source_info(store_id)

    def get_all_simulated_store_ids(self) -> List[str]:
        return get_simulated_store_ids()


simulated_data_service = SimulatedDataService()
