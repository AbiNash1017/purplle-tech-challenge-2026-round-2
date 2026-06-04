import os
import yaml
import logging
import requests
import asyncio
import threading
from typing import Dict, Any, List
from redis import asyncio as aioredis

from pipeline.detect import CVProcessor
from pipeline.tracker.reid_buffer import ReIDTracker
from pipeline.heartbeat import HeartbeatSender

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

API_URL = os.getenv("API_URL", "http://localhost:8000")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Fetch zones from API
def fetch_zones_from_api(store_id: str) -> List[Dict[str, Any]]:
    try:
        url = f"{API_URL}/api/v1/stores/{store_id}/zones"
        r = requests.get(url, timeout=5.0)
        if r.status_code == 200:
            return r.json()
        logger.error(f"Failed to fetch zones for {store_id}: HTTP {r.status_code}")
    except Exception as e:
        logger.error(f"Failed to fetch zones for {store_id}: {e}")
    return []

def run_camera_thread(store_id: str, cam_config: Dict[str, Any], zones: List[Dict[str, Any]], reid_tracker: ReIDTracker):
    cam_id = cam_config["id"]
    video_file = cam_config["file"]
    
    # Path relative to the pipeline directory
    pipeline_dir = os.path.dirname(os.path.abspath(__file__))
    video_path = os.path.join(pipeline_dir, video_file)
    
    logger.info(f"Launching worker for store {store_id} camera {cam_id} using video {video_path}")
    
    # Start heartbeat sender in a separate daemon thread
    heartbeat_sender = HeartbeatSender(store_id, cam_id, API_URL)
    hb_thread = threading.Thread(target=heartbeat_sender.start, daemon=True)
    hb_thread.start()
    
    # Start CV Processor
    processor = CVProcessor(store_id, cam_id, video_path, API_URL, zones, reid_tracker)
    
    # Run the processor
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(processor.process_video())
    except Exception as e:
        logger.error(f"Error in processor thread for {store_id}:{cam_id}: {e}")
    finally:
        heartbeat_sender.stop()
        loop.close()

async def main():
    logger.info("Initializing Store Intelligence Pipeline Orchestrator...")
    
    # Load configuration
    pipeline_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(pipeline_dir, "config", "cameras.yaml")
    if not os.path.exists(config_path):
        logger.error(f"Configuration file not found at {config_path}")
        return
        
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
        
    # Connect to Redis for Re-ID caching
    redis_client = None
    try:
        redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
        # Test connection
        await redis_client.ping()
        logger.info("Pipeline successfully connected to Redis for Re-ID.")
    except Exception as e:
        logger.warning(f"Pipeline could not connect to Redis: {e}. Re-ID will use local memory.")
        redis_client = None
        
    reid_tracker = ReIDTracker(redis_client=redis_client)
    
    threads = []
    stores = config.get("stores", {})
    
    for store_id, store_config in stores.items():
        # Fetch zone geometries
        zones = fetch_zones_from_api(store_id)
        if not zones:
            logger.warning(f"No zones fetched for store {store_id}. Spatial tracking might fail.")
            
        cameras = store_config.get("cameras", [])
        for cam in cameras:
            t = threading.Thread(
                target=run_camera_thread,
                args=(store_id, cam, zones, reid_tracker),
                daemon=True
            )
            t.start()
            threads.append(t)
            
    logger.info(f"Successfully launched {len(threads)} camera processing threads. Press Ctrl+C to stop.")
    
    # Keep main thread alive
    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down orchestrator...")
        
    if redis_client:
        await redis_client.close()

if __name__ == "__main__":
    asyncio.run(main())
