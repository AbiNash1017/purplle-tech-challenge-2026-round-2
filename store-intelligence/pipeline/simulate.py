import os
import json
import time
import argparse
import requests
import logging
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

def parse_args():
    parser = argparse.ArgumentParser(description="Simulate real-time YOLOv11 visual tracking events.")
    parser.add_argument("--file", type=str, default="data/sample_eventsbe42122.jsonl", help="Path to JSONL events file")
    parser.add_argument("--api-url", type=str, default="http://localhost:8000", help="FastAPI backend URL")
    parser.add_argument("--speed", type=float, default=1.0, help="Replay speed multiplier (e.g. 2.0 = twice as fast)")
    parser.add_argument("--loop", action="store_true", help="Loop the event simulation infinitely")
    parser.add_argument("--store", type=str, default=None, help="Force override store_id (e.g. ST1076)")
    return parser.parse_args()

def run_simulation():
    args = parse_args()
    
    pipeline_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(pipeline_dir, args.file)
    
    if not os.path.exists(file_path):
        logger.error(f"Event file not found at {file_path}")
        return
        
    logger.info(f"Starting simulation from {file_path}")
    logger.info(f"Target API: {args.api_url}/api/v1/events")
    logger.info(f"Speed multiplier: {args.speed}x")
    
    while True:
        events = []
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    events.append(json.loads(line.strip()))
                    
        if not events:
            logger.error("No events found in file.")
            break
            
        logger.info(f"Loaded {len(events)} events for simulation replay.")
        
        # Sort events by timestamp to ensure chronological replay
        def get_timestamp(e):
            ts_str = e.get("event_timestamp") or e.get("event_time") or e.get("queue_join_ts") or e.get("timestamp")
            if ts_str:
                try:
                    return datetime.fromisoformat(ts_str)
                except ValueError:
                    pass
            return datetime.now(timezone.utc)
            
        events.sort(key=get_timestamp)
        
        last_ts = None
        for i, event in enumerate(events):
            current_ts = get_timestamp(event)
            
            # Apply store override if requested
            if args.store:
                if "store_code" in event:
                    event["store_code"] = args.store
                if "store_id" in event:
                    event["store_id"] = args.store
                    
            # Calculate sleep delay based on difference in event times
            if last_ts is not None:
                delay = (current_ts - last_ts).total_seconds()
                # Adjust for speed multiplier
                delay = max(delay / args.speed, 0.0)
                if delay > 0:
                    logger.debug(f"Sleeping for {delay:.2f} seconds before next event")
                    time.sleep(delay)
                    
            last_ts = current_ts
            
            # Update event timestamp to current time for live dashboard streaming
            now_iso = datetime.now(timezone.utc).isoformat()
            if "event_timestamp" in event:
                event["event_timestamp"] = now_iso
            if "event_time" in event:
                event["event_time"] = now_iso
            if "timestamp" in event:
                event["timestamp"] = now_iso
                
            # If queue completed/abandoned, adjust queue exit timestamp as well
            if event.get("queue_exit_ts"):
                event["queue_exit_ts"] = now_iso
                
            logger.info(f"Replaying event {i+1}/{len(events)}: {event.get('event_type')} for track {event.get('track_id') or event.get('id_token')}")
            
            # Send to Ingestion API
            try:
                r = requests.post(f"{args.api_url}/api/v1/events", json=event, timeout=3.0)
                if r.status_code != 200:
                    logger.error(f"Failed to post simulated event: {r.status_code} - {r.text}")
            except Exception as e:
                logger.error(f"Error posting simulated event: {e}")
                
        if not args.loop:
            break
            
        logger.info("Simulation loop complete. Restarting in 5 seconds...")
        time.sleep(5)

if __name__ == "__main__":
    run_simulation()
