import time
import requests
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class HeartbeatSender:
    def __init__(self, store_id: str, camera_id: str, api_url: str, interval_seconds: int = 30):
        self.store_id = store_id
        self.camera_id = camera_id
        self.api_url = api_url
        self.interval = interval_seconds
        self.running = False

    def start(self):
        self.running = True
        logger.info(f"Starting heartbeat for {self.store_id}:{self.camera_id}...")
        while self.running:
            try:
                payload = {
                    "event_type": "heartbeat",
                    "store_id": self.store_id,
                    "track_id": "HEARTBEAT",
                    "camera_id": self.camera_id,
                    "timestamp": datetime.utcnow().isoformat(),
                    "is_staff": False
                }
                r = requests.post(f"{self.api_url}/api/v1/events", json=payload, timeout=2.0)
                if r.status_code != 200:
                    logger.debug(f"Heartbeat failed: {r.status_code}")
            except Exception as e:
                logger.debug(f"Heartbeat send error: {e}")
            time.sleep(self.interval)

    def stop(self):
        self.running = False
