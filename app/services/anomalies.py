import os
import logging
from datetime import datetime, timezone, timedelta
from typing import List
from app.models.schemas import AnomalyDetail
from app.core.database import get_db_client
from app.services.simulated_data import simulated_data_service
from app.services.analytics import analytics_service

logger = logging.getLogger(__name__)

class AnomaliesService:
    def __init__(self):
        self.db = None

    def _get_db(self):
        if self.db is None:
            self.db = get_db_client()
        return self.db

    async def get_store_anomalies(self, store_id: str) -> List[AnomalyDetail]:
        db = self._get_db()
        
        # 1. Fetch current metrics
        if simulated_data_service.has_data(store_id):
            metrics = await simulated_data_service.get_store_metrics(store_id)
            is_simulated = True
        else:
            metrics = await analytics_service.get_store_metrics(store_id)
            is_simulated = False

        if not metrics:
            return []

        anomalies = []
        now = datetime.now(timezone.utc)

        # -------------------------------------------------------------------
        # Check A: Queue Spike
        # -------------------------------------------------------------------
        if metrics.queue_depth > 5 or metrics.queue_wait_seconds_avg > 180.0:
            severity = "CRITICAL" if metrics.queue_depth > 8 else "WARN"
            anomalies.append(AnomalyDetail(
                type="queue_spike",
                severity=severity,
                message=f"Queue spike detected: current depth is {metrics.queue_depth} with average wait time {metrics.queue_wait_seconds_avg:.1f}s.",
                suggested_action="Open an additional billing counter to reduce wait time.",
                timestamp=now
            ))

        # -------------------------------------------------------------------
        # Check B: Conversion Drop vs 7-day Avg
        # -------------------------------------------------------------------
        # Calculate historical 7-day average conversion rate from the database.
        seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
        try:
            total_exits_7d = await db["spatial_events"].count_documents({
                "store_id": store_id,
                "event_type": "exit",
                "timestamp": {"$gte": seven_days_ago}
            })
            total_pos_7d = await db["pos_transactions"].count_documents({
                "store_id": store_id,
                "timestamp": {"$gte": seven_days_ago}
            })
            if total_exits_7d >= 10:
                avg_7d = float(total_pos_7d) / float(total_exits_7d)
            else:
                avg_7d = 0.15  # baseline 15%
        except Exception:
            avg_7d = 0.15

        # Cap avg_7d to reasonable limits
        avg_7d = min(max(avg_7d, 0.05), 1.0)
        
        current_rate = metrics.pos_correlation_rate
        # Trigger if current conversion rate is less than 80% of 7-day average
        if current_rate < 0.8 * avg_7d and avg_7d > 0:
            severity = "CRITICAL" if current_rate < 0.5 * avg_7d else "WARN"
            drop_pct = (1.0 - (current_rate / avg_7d)) * 100
            anomalies.append(AnomalyDetail(
                type="conversion_drop",
                severity=severity,
                message=f"Conversion drop detected: current conversion rate is {current_rate*100:.1f}%, which is a {drop_pct:.1f}% drop compared to the 7-day average of {avg_7d*100:.1f}%.",
                suggested_action="Verify POS terminal connectivity or deploy target coupon offers near the exit.",
                timestamp=now
            ))

        # -------------------------------------------------------------------
        # Check C: Dead Zone (no visits in 30 min)
        # -------------------------------------------------------------------
        cutoff_duration = timedelta(minutes=30)
        
        if is_simulated:
            # In simulated mode, parse the max event timestamp to use as ref time
            events = simulated_data_service._load_events(store_id)
            if events:
                max_ts = None
                for e in events:
                    ts_str = e.get("timestamp")
                    if ts_str:
                        try:
                            # Normalize UTC 'Z' representation for datetime parsing
                            dt_str = ts_str.replace("Z", "+00:00")
                            dt = datetime.fromisoformat(dt_str)
                            if max_ts is None or dt > max_ts:
                                max_ts = dt
                        except Exception:
                            pass
                ref_now = max_ts if max_ts else datetime.now(timezone.utc)
            else:
                ref_now = datetime.now(timezone.utc)
        else:
            ref_now = datetime.now(timezone.utc)

        cutoff_30m = ref_now - cutoff_duration

        # Fetch configured zones
        zones = []
        try:
            zones_cursor = db["zones"].find({"store_id": store_id})
            zones = await zones_cursor.to_list(length=100)
        except Exception:
            pass

        # Fallback zone configuration if database empty
        if not zones:
            from app.services.simulated_data import _ZONE_DEFS
            zones = [{"zone_id": z["zone_id"], "zone_name": z["zone_name"], "zone_type": z["zone_type"]} for z in _ZONE_DEFS.get(store_id, [])]

        # Scan shelf & display zones for visits in the 30-min window
        for zone in zones:
            zone_id = zone.get("zone_id")
            zone_name = zone.get("zone_name")
            zone_type = zone.get("zone_type", "SHELF")
            
            if zone_type not in ["SHELF", "DISPLAY"]:
                continue

            has_visit = False
            if is_simulated:
                # Scan local list
                for e in events:
                    if e.get("is_staff", False):
                        continue
                    if e.get("zone_id") == zone_id or e.get("zone_name") == zone_name:
                        ts_str = e.get("timestamp")
                        if ts_str:
                            try:
                                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                                if cutoff_30m <= dt <= ref_now:
                                    has_visit = True
                                    break
                            except Exception:
                                pass
            else:
                # Query MongoDB collection
                try:
                    count = await db["spatial_events"].count_documents({
                        "store_id": store_id,
                        "zone_id": zone_id,
                        "event_type": {"$in": ["zone_entered", "zone_update"]},
                        "timestamp": {"$gte": cutoff_30m, "$lte": ref_now}
                    })
                    if count > 0:
                        has_visit = True
                except Exception:
                    pass

            if not has_visit:
                anomalies.append(AnomalyDetail(
                    type="dead_zone",
                    severity="INFO",
                    message=f"Dead zone detected: '{zone_name}' (Zone {zone_id}) has received zero customer visits in the last 30 minutes.",
                    suggested_action=f"Re-arrange shelf displays or place a promotional sign in '{zone_name}' to attract customers.",
                    timestamp=now
                ))

        return anomalies

anomalies_service = AnomaliesService()
