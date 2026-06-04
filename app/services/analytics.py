import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any
from app.core.database import get_db_client
from app.models.schemas import MetricsResponse, ZoneHeatmapPoint

logger = logging.getLogger(__name__)

class AnalyticsService:
    def __init__(self):
        self.db = None

    def _get_db(self):
        if self.db is None:
            self.db = get_db_client()
        return self.db

    async def get_store_metrics(self, store_id: str) -> MetricsResponse:
        database = self._get_db()
        now = datetime.now(timezone.utc)
        
        # We define a 2-minute cutoff for active customers
        active_cutoff = now - timedelta(minutes=2)

        # 1. Active Customers (count customer sessions seen in the last 2 minutes that haven't exited)
        active_customers = await database["customer_sessions"].count_documents({
            "store_id": store_id,
            "last_seen": {"$gte": active_cutoff},
            "visit_segments": {
                "$elemMatch": {"exited_at": None}
            }
        })

        # 2. Footfall Count (Total unique customer sessions today or overall in the simulation)
        # For simulation simplicity, we count all sessions in the store
        footfall_count = await database["customer_sessions"].count_documents({
            "store_id": store_id
        })

        # 3. Average Dwell Time (across completed segments)
        dwell_pipeline = [
            {"$match": {"store_id": store_id}},
            {"$unwind": "$visit_segments"},
            {"$match": {"visit_segments.exited_at": {"$ne": None}}},
            {
                "$project": {
                    "duration": {
                        "$divide": [
                            {"$subtract": ["$visit_segments.exited_at", "$visit_segments.entered_at"]},
                            1000  # Convert ms to seconds
                        ]
                    }
                }
            },
            {
                "$group": {
                    "_id": None,
                    "avg_dwell": {"$avg": "$duration"}
                }
            }
        ]
        
        avg_dwell_seconds = 0.0
        try:
            dwell_result = await database["customer_sessions"].aggregate(dwell_pipeline).to_list(1)
            if dwell_result:
                avg_dwell_seconds = float(dwell_result[0]["avg_dwell"] or 0.0)
        except Exception as e:
            logger.error(f"Error aggregating dwell time: {e}")

        # 4. Queue Metrics (Wait time, abandon rate, queue depth)
        queue_match = {
            "store_id": store_id,
            "event_type": {"$in": ["queue_completed", "queue_abandoned"]}
        }
        
        queue_pipeline = [
            {"$match": queue_match},
            {
                "$group": {
                    "_id": None,
                    "avg_wait": {"$avg": "$wait_seconds"},
                    "total_count": {"$sum": 1},
                    "abandoned_count": {
                        "$sum": {"$cond": [{"$eq": ["$event_type", "queue_abandoned"]}, 1, 0]}
                    }
                }
            }
        ]
        
        queue_wait_seconds_avg = 0.0
        queue_abandon_rate = 0.0
        try:
            queue_result = await database["spatial_events"].aggregate(queue_pipeline).to_list(1)
            if queue_result and queue_result[0]["total_count"] > 0:
                q_data = queue_result[0]
                queue_wait_seconds_avg = float(q_data["avg_wait"] or 0.0)
                queue_abandon_rate = float(q_data["abandoned_count"] / q_data["total_count"])
        except Exception as e:
            logger.error(f"Error aggregating queue metrics: {e}")

        # Live Queue Depth (Tracks currently in the billing zone within the last 1 minute)
        billing_cutoff = now - timedelta(minutes=1)
        queue_depth = await database["spatial_events"].count_documents({
            "store_id": store_id,
            "event_type": "zone_entered",
            "zone_type": "BILLING",
            "timestamp": {"$gte": billing_cutoff}
        })
        # If no active entries, try finding queue completed snapshots
        if queue_depth == 0:
            snapshot_event = await database["spatial_events"].find_one(
                {"store_id": store_id, "event_type": "queue_completed"},
                sort=[("timestamp", -1)]
            )
            if snapshot_event:
                queue_depth = max(snapshot_event.get("queue_position_at_join", 1) - 1, 0)

        # 5. POS Correlation Rate
        # Calculate how many exit events correlate with a POS transaction within +- 60 seconds
        exits_query = {
            "store_id": store_id,
            "event_type": "exit"
        }
        
        total_exits = await database["spatial_events"].count_documents(exits_query)
        correlated_exits = 0
        
        if total_exits > 0:
            # Let's count them by looking up POS transactions in batch or per exit
            # For efficiency in production, we can run an aggregation pipeline
            correlation_pipeline = [
                {"$match": exits_query},
                {
                    "$lookup": {
                        "from": "pos_transactions",
                        "let": {"exit_ts": "$timestamp", "s_id": "$store_id"},
                        "pipeline": [
                            {
                                "$match": {
                                    "$expr": {
                                        "$and": [
                                            {"$eq": ["$store_id", "$$s_id"]},
                                            {"$gte": ["$timestamp", {"$subtract": ["$$exit_ts", 60000]}]}, # -60s
                                            {"$lte": ["$timestamp", {"$add": ["$$exit_ts", 60000]}]}      # +60s
                                        ]
                                    }
                                }
                            }
                        ],
                        "as": "matched_transactions"
                    }
                },
                {
                    "$project": {
                        "is_correlated": {"$cond": [{"$gt": [{"$size": "$matched_transactions"}, 0]}, 1, 0]}
                    }
                },
                {
                    "$group": {
                        "_id": None,
                        "correlated_count": {"$sum": "$is_correlated"}
                    }
                }
            ]
            try:
                corr_result = await database["spatial_events"].aggregate(correlation_pipeline).to_list(1)
                if corr_result:
                    correlated_exits = corr_result[0]["correlated_count"]
            except Exception as e:
                logger.error(f"Error calculating POS correlation rate: {e}")

        pos_correlation_rate = float(correlated_exits / total_exits) if total_exits > 0 else 0.0

        # 6. Zone breakdown (Total Dwell Time & Visits per zone)
        # Seed all zones for the store to ensure we return points even with 0 metrics
        zones_cursor = database["zones"].find({"store_id": store_id})
        zones_list = await zones_cursor.to_list(length=100)
        zone_map = {z["zone_id"]: {"name": z["zone_name"], "type": z["zone_type"], "dwell": 0.0, "visits": 0} for z in zones_list}

        # Calculate visits & total duration per zone
        zone_pipeline = [
            {
                "$match": {
                    "store_id": store_id,
                    "event_type": "zone_exited",
                    "zone_id": {"$exists": True, "$ne": None}
                }
            },
            {
                "$group": {
                    "_id": "$zone_id",
                    "total_visits": {"$sum": 1},
                    # Dwell time estimated from some events or calculated directly if wait/dwell fields are present.
                    # Or we calculate using exit - enter matching.
                    # As a simpler reliable fallback, we check if wait_seconds/dwell_seconds are present or compute it.
                    "total_dwell": {"$sum": {"$ifNull": ["$wait_seconds", 30]}} # Default to 30s per visit if not specified
                }
            }
        ]
        
        try:
            zone_results = await database["spatial_events"].aggregate(zone_pipeline).to_list(100)
            for r in zone_results:
                z_id = r["_id"]
                if z_id in zone_map:
                    zone_map[z_id]["visits"] = r["total_visits"]
                    zone_map[z_id]["dwell"] = float(r["total_dwell"])
        except Exception as e:
            logger.error(f"Error calculating zone breakdown: {e}")
            
        zone_breakdown = [
            ZoneHeatmapPoint(
                zone_id=z_id,
                zone_name=z_data["name"],
                zone_type=z_data["type"],
                dwell_seconds=z_data["dwell"],
                visit_count=z_data["visits"]
            )
            for z_id, z_data in zone_map.items()
        ]

        return MetricsResponse(
            store_id=store_id,
            timestamp=now,
            active_customers=active_customers,
            footfall_count=footfall_count,
            avg_dwell_seconds=avg_dwell_seconds,
            queue_wait_seconds_avg=queue_wait_seconds_avg,
            queue_abandon_rate=queue_abandon_rate,
            queue_depth=queue_depth,
            pos_correlation_rate=pos_correlation_rate,
            zone_breakdown=zone_breakdown
        )

    async def get_zone_heatmap(self, store_id: str) -> List[Dict[str, Any]]:
        # Returns coordinates and intensity for heatmap visualization
        database = self._get_db()
        pipeline = [
            {
                "$match": {
                    "store_id": store_id,
                    "event_type": {"$in": ["zone_entered", "zone_exited", "zone_update"]},
                    "location": {"$ne": None}
                }
            },
            {
                "$project": {
                    "coordinates": "$location.coordinates",
                    "weight": {"$ifNull": ["$wait_seconds", 10]}
                }
            },
            {
                "$group": {
                    "_id": "$coordinates",
                    "intensity": {"$sum": "$weight"}
                }
            },
            {
                "$project": {
                    "x": {"$arrayElemAt": ["$_id", 0]},
                    "y": {"$arrayElemAt": ["$_id", 1]},
                    "intensity": 1,
                    "_id": 0
                }
            }
        ]
        try:
            return await database["spatial_events"].aggregate(pipeline).to_list(1000)
        except Exception as e:
            logger.error(f"Error fetching zone heatmap points: {e}")
            return []

analytics_service = AnalyticsService()
