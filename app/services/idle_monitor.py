import asyncio
import logging
import json
from datetime import datetime, timezone
from app.core.database import get_redis_client
from app.core.pub_sub import pubsub_broker

logger = logging.getLogger(__name__)


class IdleMonitorService:
    def __init__(self):
        self.running = False
        self.task = None

    async def start(self):
        if self.running:
            return
        self.running = True
        self.task = asyncio.create_task(self._monitor_loop())
        logger.info("Idle monitor service started.")

    async def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        logger.info("Idle monitor service stopped.")

    async def _monitor_loop(self):
        redis = get_redis_client()  # Upstash REST client
        stores = ["ST1076", "ST1008"]

        while self.running:
            try:
                await asyncio.sleep(15)
                now = datetime.now(timezone.utc)

                for store_id in stores:
                    # Check last activity key from Upstash REST
                    last_act_str = await redis.get(f"last_activity:{store_id}")
                    is_idle = True

                    if last_act_str:
                        try:
                            last_act = datetime.fromisoformat(last_act_str)
                            # Ensure timezone-aware for comparison
                            if last_act.tzinfo is None:
                                last_act = last_act.replace(tzinfo=timezone.utc)
                            if (now - last_act).total_seconds() < 60:
                                is_idle = False
                        except (ValueError, TypeError):
                            pass

                    if is_idle:
                        # Publish idle event via in-process broker
                        channel = f"live_tracks:{store_id}"
                        idle_event = {
                            "event_type": "store_idle",
                            "store_id": store_id,
                            "timestamp": now.isoformat()
                        }
                        await pubsub_broker.publish(channel, json.dumps(idle_event))
                        logger.debug(f"Store {store_id} is idle. Broadcasted store_idle event.")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in idle monitor loop: {e}")


idle_monitor_service = IdleMonitorService()
