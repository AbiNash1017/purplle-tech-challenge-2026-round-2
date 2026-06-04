import logging
import hashlib
import json
from datetime import datetime, timezone
from typing import Dict, Any, List
from fastapi import BackgroundTasks
from app.core.database import get_db_client, get_redis_client
from app.core.pub_sub import pubsub_broker
from app.models.schemas import TrackingEvent

logger = logging.getLogger(__name__)

class IngestionService:
    def __init__(self):
        self.db = None
        self.redis = None

    def _get_connections(self):
        if self.db is None:
            self.db = get_db_client()
        if self.redis is None:
            self.redis = get_redis_client()

    def generate_event_hash(self, event: TrackingEvent) -> str:
        # Create a unique hash for the event based on core fields to prevent duplicate ingestions
        ts_str = event.timestamp.isoformat() if isinstance(event.timestamp, datetime) else str(event.timestamp)
        payload = f"{event.store_id}:{event.track_id}:{event.event_type}:{ts_str}"
        if event.zone_id:
            payload += f":{event.zone_id}"
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    async def check_idempotency(self, event: TrackingEvent) -> bool:
        self._get_connections()
        if event.event_id:
            redis_key = f"idempotency:{event.event_id}"
        else:
            event_hash = self.generate_event_hash(event)
            redis_key = f"idempotency:{event_hash}"
        # Set key with 10s expiration
        is_new = await self.redis.set(redis_key, "1", ex=10, nx=True)
        return bool(is_new)

    async def ingest_event(self, event: TrackingEvent, background_tasks: BackgroundTasks):
        self._get_connections()
        
        # 1. Idempotency Check
        is_new = await self.check_idempotency(event)
        if not is_new:
            logger.info(f"Duplicate event detected for track {event.track_id} type {event.event_type}. Skipping.")
            return True # Return true to simulate successful handling without duplication
            
        # Convert event to dict for storage / pubsub
        event_dict = event.model_dump()
        # Serialize datetime fields for Redis JSON
        for k, v in event_dict.items():
            if isinstance(v, datetime):
                event_dict[k] = v.isoformat()

        # 2. Process Staff vs Customer Routing
        if event.is_staff:
            logger.info(f"Routing staff track {event.track_id} to staff collection.")
            background_tasks.add_task(self._save_staff_event, event_dict)
            # We don't publish staff tracks to the live dashboard websocket to meet the "staff are excluded" requirement
            return True

        # 3. Publish to in-process Pub/Sub for WebSockets
        channel = f"live_tracks:{event.store_id}"
        try:
            await pubsub_broker.publish(channel, json.dumps(event_dict))
            # Also update heartbeat timestamps in Upstash via REST
            now_iso = datetime.now(timezone.utc).isoformat()
            await self.redis.set(f"heartbeat:{event.store_id}:{event.camera_id}", now_iso, ex=90)
            await self.redis.set(f"last_activity:{event.store_id}", now_iso)
        except Exception as e:
            logger.error(f"Pub/Sub publish or heartbeat error: {e}")

        # 4. Save Customer Event to MongoDB in Background
        background_tasks.add_task(self._save_spatial_event, event_dict)
        return True

    async def _save_spatial_event(self, event_dict: Dict[str, Any]):
        try:
            self._get_connections()
            # Convert ISO string back to datetime for MongoDB
            event_dict["timestamp"] = datetime.fromisoformat(event_dict["timestamp"])
            if event_dict.get("queue_join_ts"):
                event_dict["queue_join_ts"] = datetime.fromisoformat(event_dict["queue_join_ts"])
            if event_dict.get("queue_served_ts"):
                event_dict["queue_served_ts"] = datetime.fromisoformat(event_dict["queue_served_ts"])
            if event_dict.get("queue_exit_ts"):
                event_dict["queue_exit_ts"] = datetime.fromisoformat(event_dict["queue_exit_ts"])
                
            await self.db["spatial_events"].insert_one(event_dict)
            
            # Also manage customer session collection for re-entry session tracking
            await self._update_customer_session(event_dict)
        except Exception as e:
            logger.error(f"Failed to save spatial event in background: {e}")

    async def _save_staff_event(self, event_dict: Dict[str, Any]):
        try:
            self._get_connections()
            event_dict["timestamp"] = datetime.fromisoformat(event_dict["timestamp"])
            # Update staff track info:upsert record for the staff track
            await self.db["staff_tracks"].update_one(
                {"store_id": event_dict["store_id"], "track_id": event_dict["track_id"]},
                {
                    "$set": {
                        "last_seen": event_dict["timestamp"],
                        "reason": "detected"
                    },
                    "$setOnInsert": {
                        "first_seen": event_dict["timestamp"],
                        "store_id": event_dict["store_id"],
                        "track_id": event_dict["track_id"]
                    }
                },
                upsert=True
            )
        except Exception as e:
            logger.error(f"Failed to save staff event in background: {e}")

    async def _update_customer_session(self, event: Dict[str, Any]):
        # Update customer_sessions to manage re-entry segments
        store_id = event["store_id"]
        track_id = event["track_id"]
        timestamp = event["timestamp"]
        event_type = event["event_type"]
        
        if event_type in ("entry", "re_entry"):
            # Start new segment or session
            update_ops = {
                "$set": {
                    "last_seen": timestamp,
                    "group_id": event.get("group_id"),
                    "group_size": event.get("group_size"),
                    "demographics": {
                        "gender": event.get("gender"),
                        "age": event.get("age"),
                        "age_bucket": event.get("age_bucket")
                    }
                },
                "$setOnInsert": {
                    "first_seen": timestamp,
                    "reentry_count": 0,
                    "visit_segments": []
                },
                "$push": {
                    "visit_segments": {
                        "entered_at": timestamp,
                        "exited_at": None
                    }
                }
            }
            if event_type == "re_entry":
                update_ops["$inc"] = {"reentry_count": 1}

            await self.db["customer_sessions"].update_one(
                {"store_id": store_id, "id_token": track_id},
                update_ops,
                upsert=True
            )
        elif event_type == "exit":
            # Close the last open segment
            await self.db["customer_sessions"].update_one(
                {
                    "store_id": store_id, 
                    "id_token": track_id, 
                    "visit_segments.exited_at": None
                },
                {
                    "$set": {
                        "last_seen": timestamp,
                        "visit_segments.$.exited_at": timestamp
                    }
                }
            )
        else:
            # Just update last seen for intermediate events
            await self.db["customer_sessions"].update_one(
                {"store_id": store_id, "id_token": track_id},
                {"$set": {"last_seen": timestamp}}
            )

ingestion_service = IngestionService()
