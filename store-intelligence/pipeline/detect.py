import os
import cv2
import time
import uuid
import asyncio
import logging
import requests
import numpy as np
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, timezone

from pipeline.tracker.staff_filter import StaffFilter
from pipeline.tracker.reid_buffer import ReIDTracker
from pipeline.tracker.group_detector import GroupDetector

logger = logging.getLogger(__name__)

# Store-specific uniform specs (mirrors process_videos.py)
_UNIFORM_SPEC = {
    "ST1076": {  # All-black: low V AND low S
        "lower":     np.array([0,   0,   0]),
        "upper":     np.array([180, 80,  50]),
        "ratio_min": 0.25,
        "label":     "black",
    },
    "ST1008": {  # Hot-pink / magenta: narrow hue, high S+V
        "lower":     np.array([150, 80,  60]),
        "upper":     np.array([170, 255, 255]),
        "ratio_min": 0.15,
        "label":     "pink/magenta",
    },
}

# Try loading Ultralytics YOLOv11
HAS_YOLO = False
try:
    from ultralytics import YOLO
    HAS_YOLO = True
except ImportError:
    logger.warning("Ultralytics package not found. CV Pipeline will run in SIMULATED mode.")

class CVProcessor:
    # Billing dwell threshold (video-time seconds)
    BILLING_DWELL_SECS  = 10.0
    # Re-entry window: same person returning within 10 min is a re-entry, not new entry
    REENTRY_WINDOW_SECS = 600.0

    def __init__(self, store_id: str, camera_id: str, video_path: str, api_url: str,
                 zones: List[Dict[str, Any]], reid_tracker: ReIDTracker):
        self.store_id   = store_id
        self.camera_id  = camera_id
        self.video_path = video_path
        self.api_url    = api_url
        self.zones      = zones
        self.reid_tracker = reid_tracker

        # Formatted store_code for schema alignment (e.g. "store_1076")
        self.store_code = f"store_{store_id.lstrip('ST').lstrip('0') or store_id}"

        # Sub-trackers
        self.staff_filter   = StaffFilter(store_id=store_id)
        self.group_detector = GroupDetector()

        # Active tracks: id_token → {zone_id, entered_time, last_seen, is_staff, x, y}
        self.active_tracks: Dict[str, Dict] = {}

        # Billing dwell: id_token → video-ts of first billing-zone entry
        self._billing_enter:     Dict[str, float] = {}
        self._billed_this_visit: set              = set()

        # Re-entry guard: id_token → video-ts of exit
        self._exited_at: Dict[str, float] = {}

        # Load YOLO model if available
        self.model = None
        if HAS_YOLO:
            try:
                self.model = YOLO("yolo11n.pt")
                logger.info(f"Loaded YOLOv11 model for camera {camera_id} ({store_id})")
            except Exception as e:
                logger.error(f"Error loading YOLOv11: {e}. Falling back to simulation.")
                self.model = None

    def _is_point_in_polygon(self, x: float, y: float, polygon: List[List[float]]) -> bool:
        """
        Ray-casting algorithm to determine if a normalized [x, y] is inside a zone polygon.
        """
        n = len(polygon)
        inside = False
        p1x, p1y = polygon[0]
        for i in range(n + 1):
            p2x, p2y = polygon[i % n]
            if y > min(p1y, p2y):
                if y <= max(p1y, p2y):
                    if x <= max(p1x, p2x):
                        if p1y != p2y:
                            xints = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                        if p1x == p2x or x <= xints:
                            inside = not inside
            p1x, p1y = p2x, p2y
        return inside

    def get_current_zone(self, x: float, y: float) -> Optional[Dict[str, Any]]:
        """
        Returns the zone dictionary where the point [x, y] resides.
        """
        for zone in self.zones:
            coords = zone.get("geometry", {}).get("coordinates", [])
            if coords:
                # GeoJSON Polygon coordinates are usually [[[x1, y1], [x2, y2], ...]]
                poly = coords[0]
                if self._is_point_in_polygon(x, y, poly):
                    return zone
        return None

    def classify_staff_uniform(self, crop: np.ndarray, bbox_h: int = 0) -> bool:
        """
        Precision uniform classifier using store-specific HSV ranges.
        Returns True only when the torso shows a clear colour match.
        Uses composite scoring via StaffFilter.apply_uniform_score().

        ST1076 — All-black: V < 50 AND S < 80, ≥ 25 % of torso.
        ST1008 — Hot-pink: Hue 150-170, S > 80, V > 60, ≥ 15 % of torso.
        """
        if crop is None or crop.size == 0:
            return False
        if bbox_h > 0 and bbox_h < 60:
            return False

        try:
            h, w = crop.shape[:2]
            if h < 20 or w < 10:
                return False

            torso = crop[int(h * 0.15): int(h * 0.55), :]
            if torso.size == 0:
                return False

            hsv   = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
            total = torso.shape[0] * torso.shape[1]
            if total == 0:
                return False

            spec  = _UNIFORM_SPEC.get(self.store_id, _UNIFORM_SPEC["ST1008"])
            mask  = cv2.inRange(hsv, spec["lower"], spec["upper"])
            ratio = cv2.countNonZero(mask) / total

            if ratio >= spec["ratio_min"]:
                logger.info(
                    f"Uniform match ({spec['label']} ratio={ratio:.3f}) cam={self.camera_id}"
                )
                return True
        except Exception as e:
            logger.error(f"Uniform classifier error: {e}")
        return False

    def emit_event(self, event_type: str, track_id: str, data: Dict[str, Any]):
        """
        POSTs a tracking event to the backend.  Field names match sample_events.jsonl:
          id_token, store_code, event_timestamp, gender_pred, age_pred,
          age_bucket, is_face_hidden, group_id, group_size.
        """
        payload = {
            "event_type":      event_type,
            "id_token":        track_id,
            "store_code":      self.store_code,
            "store_id":        self.store_id,
            "camera_id":       self.camera_id,
            "event_timestamp": datetime.now(timezone.utc).isoformat(),
            "gender_pred":     data.pop("gender_pred", None),
            "age_pred":        data.pop("age_pred", None),
            "age_bucket":      data.pop("age_bucket", None),
            "is_face_hidden":  data.pop("is_face_hidden", False),
            "group_id":        data.pop("group_id", None),
            "group_size":      data.pop("group_size", None),
            **data,
        }

        try:
            r = requests.post(f"{self.api_url}/api/v1/events", json=payload, timeout=3.0)
            if r.status_code != 200:
                logger.error(f"API ingestion error: {r.status_code} - {r.text}")
        except Exception as e:
            logger.error(f"Failed to post event to backend: {e}")

    # ── Billing-dwell helpers ─────────────────────────────────────────────
    def _update_billing_dwell(self, id_token: str, zone_type: Optional[str],
                               ts: float, is_staff: bool):
        """Emit billed_customer event when a non-staff person dwells ≥ 10 s at billing."""
        if is_staff:
            self._billing_enter.pop(id_token, None)
            self._billed_this_visit.discard(id_token)
            return
        if zone_type == "BILLING":
            if id_token not in self._billing_enter:
                self._billing_enter[id_token] = ts
            else:
                dwell = ts - self._billing_enter[id_token]
                if dwell >= self.BILLING_DWELL_SECS and id_token not in self._billed_this_visit:
                    self._billed_this_visit.add(id_token)
                    self.emit_event("billed_customer", id_token, {
                        "zone_id":       self.active_tracks.get(id_token, {}).get("zone_id"),
                        "zone_name":     "Billing Counter",
                        "zone_type":     "BILLING",
                        "dwell_seconds": round(dwell, 1),
                        "is_staff":      False,
                    })
                    logger.info(
                        f"billed_customer: {id_token} cam={self.camera_id} "
                        f"dwell={dwell:.1f}s"
                    )
        else:
            if id_token in self._billing_enter:
                self._billing_enter.pop(id_token, None)
                self._billed_this_visit.discard(id_token)

    async def process_video(self):
        """
        Main processing loop. Reads video, runs YOLOv11 + ByteTrack,
        performs staff filters, Re-ID, group detection, and zone intersection.
        If YOLOv11 is not loaded or file is missing, runs a robust simulation of the camera.
        """
        if self.model is None or not os.path.exists(self.video_path):
            logger.warning(f"Video file {self.video_path} not found or YOLOv11 disabled. Running camera {self.camera_id} in simulation mode.")
            await self.run_simulation()
            return

        cap = cv2.VideoCapture(self.video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_delay = 1.0 / fps
        
        frame_idx = 0
        
        logger.info(f"Starting real-time CV processing on {self.video_path} (FPS: {fps})")
        
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
                
            frame_idx += 1
            # Run inference every 3 frames for optimization
            if frame_idx % 3 != 0:
                await asyncio.sleep(0.001)
                continue
                
            timestamp_seconds = frame_idx / fps
            
            # YOLOv11 inference with built-in ByteTrack
            try:
                # classes=[0] tracks only persons
                results = self.model.track(frame, persist=True, classes=[0], verbose=False)
                
                if results and len(results) > 0 and results[0].boxes is not None:
                    boxes = results[0].boxes
                    
                    # Track IDs assigned by ByteTrack
                    track_ids = boxes.id
                    xyxys = boxes.xyxy.cpu().numpy()
                    
                    if track_ids is not None:
                        track_ids = track_ids.cpu().numpy().astype(int)
                        
                        for i, t_id in enumerate(track_ids):
                            str_tid = f"ID_{t_id}"
                            x1, y1, x2, y2 = xyxys[i]
                            
                            # Normalized centroid coordinates
                            img_h, img_w, _ = frame.shape
                            cx_norm = float((x1 + x2) / (2.0 * img_w))
                            cy_norm = float((y1 + y2) / (2.0 * img_h))
                            
                            # Crop person for Re-ID and Uniform classification
                            crop = frame[int(y1):int(y2), int(x1):int(x2)]
                            
                            # 1. Staff classification (composite score)
                            is_staff = self.staff_filter.is_staff(str_tid)
                            if not is_staff:
                                uniform_hit = self.classify_staff_uniform(crop, bbox_h=(int(y2)-int(y1)))
                                if uniform_hit:
                                    is_staff = self.staff_filter.apply_uniform_score(str_tid)
                                if not is_staff:
                                    is_staff = self.staff_filter.update_velocity(
                                        str_tid, cx_norm, cy_norm, timestamp_seconds
                                    )

                            # 2. Re-ID cross-camera tracking (customers only)
                            id_token = str_tid
                            if not is_staff:
                                id_token = await self.reid_tracker.lookup_and_register(
                                    self.store_id, crop, str_tid
                                )
                                # Lock confirmed customer
                                self.staff_filter.lock_as_customer(id_token)

                            # 3. Zone intersection
                            current_zone = self.get_current_zone(cx_norm, cy_norm)
                            zone_id   = current_zone["zone_id"]   if current_zone else None
                            zone_name = current_zone["zone_name"] if current_zone else None
                            zone_type = current_zone["zone_type"] if current_zone else None
                            is_revenue = current_zone["is_revenue_zone"] if current_zone else None

                            # 4. Billing dwell check
                            self._update_billing_dwell(id_token, zone_type, timestamp_seconds, is_staff)

                            # 5. State machine / event emission
                            prev_state = self.active_tracks.get(id_token)

                            if prev_state is None:
                                # Re-entry guard
                                exit_ts = self._exited_at.get(id_token)
                                if exit_ts is not None and (
                                    timestamp_seconds - exit_ts
                                ) <= self.REENTRY_WINDOW_SECS:
                                    self.emit_event("re_entry", id_token, {
                                        "is_staff":       is_staff,
                                        "gap_seconds":    round(timestamp_seconds - exit_ts, 1),
                                        "zone_hotspot_x": cx_norm * 1000.0,
                                        "zone_hotspot_y": cy_norm * 1000.0,
                                    })
                                else:
                                    self.emit_event("entry", id_token, {
                                        "is_staff":       is_staff,
                                        "zone_hotspot_x": cx_norm * 1000.0,
                                        "zone_hotspot_y": cy_norm * 1000.0,
                                    })

                                self.active_tracks[id_token] = {
                                    "zone_id":      zone_id,
                                    "entered_time": timestamp_seconds,
                                    "last_seen":    timestamp_seconds,
                                    "is_staff":     is_staff,
                                    "x":            cx_norm,
                                    "y":            cy_norm,
                                }

                                if zone_id:
                                    self.emit_event("zone_entered", id_token, {
                                        "zone_id":         zone_id,
                                        "zone_name":       zone_name,
                                        "zone_type":       zone_type,
                                        "is_revenue_zone": is_revenue,
                                        "zone_hotspot_x":  cx_norm * 1000.0,
                                        "zone_hotspot_y":  cy_norm * 1000.0,
                                        "is_staff":        is_staff,
                                    })
                            else:
                                # Existing track update
                                prev_zone_id = prev_state["zone_id"]
                                prev_state["last_seen"] = timestamp_seconds
                                prev_state["x"] = cx_norm
                                prev_state["y"] = cy_norm

                                if zone_id != prev_zone_id:
                                    if prev_zone_id:
                                        self.emit_event("zone_exited", id_token, {
                                            "zone_id":      prev_zone_id,
                                            "wait_seconds": int(timestamp_seconds - prev_state["entered_time"]),
                                            "is_staff":     is_staff,
                                        })
                                    if zone_id:
                                        if self.staff_filter.record_zone(id_token, zone_id):
                                            is_staff = True
                                            self.active_tracks[id_token]["is_staff"] = True

                                        self.active_tracks[id_token]["zone_id"]      = zone_id
                                        self.active_tracks[id_token]["entered_time"] = timestamp_seconds

                                        self.emit_event("zone_entered", id_token, {
                                            "zone_id":         zone_id,
                                            "zone_name":       zone_name,
                                            "zone_type":       zone_type,
                                            "is_revenue_zone": is_revenue,
                                            "zone_hotspot_x":  cx_norm * 1000.0,
                                            "zone_hotspot_y":  cy_norm * 1000.0,
                                            "is_staff":        is_staff,
                                        })
                                else:
                                    self.emit_event("zone_update", id_token, {
                                        "zone_hotspot_x": cx_norm * 1000.0,
                                        "zone_hotspot_y": cy_norm * 1000.0,
                                        "is_staff":       is_staff,
                                    })

            except Exception as e:
                logger.error(f"Inference error in frame {frame_idx}: {e}")
                
            # Grouping
            groups = self.group_detector.update_groups(self.active_tracks, timestamp_seconds)
            for tid, g_id in groups.items():
                if tid in self.active_tracks:
                    self.active_tracks[tid]["group_id"] = g_id

            # Clean up lost tracks (not seen for > 3 seconds)
            lost_ids = [
                t for t, info in self.active_tracks.items()
                if timestamp_seconds - info["last_seen"] > 3.0
            ]
            for l_id in lost_ids:
                info = self.active_tracks.pop(l_id)
                self._exited_at[l_id] = timestamp_seconds   # re-entry guard
                self.emit_event("exit", l_id, {
                    "is_staff":       info["is_staff"],
                    "zone_hotspot_x": info["x"] * 1000.0,
                    "zone_hotspot_y": info["y"] * 1000.0,
                })
                # Clean billing dwell state
                self._billing_enter.pop(l_id, None)
                self._billed_this_visit.discard(l_id)

            time.sleep(frame_delay * 3.0)

        cap.release()
        logger.info(f"Finished processing video file for camera {self.camera_id}")

    async def run_simulation(self):
        """
        Simulated camera track generation. Generates highly realistic customer/staff tracks
        and queue/billing events, which matches the visual layout and constraints.
        """
        logger.info(f"Starting simulated trajectory generator for camera {self.camera_id}")
        import random
        import asyncio
        
        sim_tracks = {} # track_id -> info
        track_counter = 100
        
        while True:
            # Randomly spawn new customers (every 10-30 seconds)
            # Or staff members (every 40 seconds)
            if random.random() < 0.15 and len(sim_tracks) < 6:
                track_counter += 1
                t_id = f"SIM_{track_counter}"
                is_staff = random.random() < 0.15 # 15% chance it's a staff member
                
                # Assign initial position close to entrance (Z07) or general
                x = random.uniform(0.0, 0.2)
                y = random.uniform(0.3, 0.7)
                
                sim_tracks[t_id] = {
                    "is_staff": is_staff,
                    "x": x,
                    "y": y,
                    "step": 0,
                    "zone_id": None,
                    "joined_queue": None,
                    "entered_time": time.time(),
                    "gender": random.choice(["M", "F"]),
                    "age": random.randint(18, 55)
                }
                
                self.emit_event("entry", t_id, {
                    "is_staff": is_staff,
                    "gender": sim_tracks[t_id]["gender"],
                    "age": sim_tracks[t_id]["age"],
                    "zone_hotspot_x": x * 1000.0,
                    "zone_hotspot_y": y * 1000.0
                })
                
            # Update position for all active simulated tracks
            finished_tracks = []
            for t_id, info in sim_tracks.items():
                info["step"] += 1
                
                # Staff moves fast and randomly; Customer stays inside zone and goes towards cashier
                if info["is_staff"]:
                    # Staff behavior: fast, uniform movement across different zones
                    info["x"] = random.uniform(0.1, 0.9)
                    info["y"] = random.uniform(0.1, 0.9)
                else:
                    # Customer behavior: progressive traversal to cashier Z06
                    if info["step"] < 5:
                        # Browse shelves
                        info["x"] = random.uniform(0.2, 0.8)
                        info["y"] = random.uniform(0.1, 0.5)
                    elif info["step"] < 8:
                        # Move to billing zone
                        info["x"] = random.uniform(0.82, 0.95) if self.store_id == "ST1076" else random.uniform(0.40, 0.60)
                        info["y"] = random.uniform(0.30, 0.70) if self.store_id == "ST1076" else random.uniform(0.45, 0.55)
                    else:
                        # Exit the store
                        finished_tracks.append(t_id)
                        continue
                        
                current_zone = self.get_current_zone(info["x"], info["y"])
                zone_id = current_zone["zone_id"] if current_zone else None
                zone_name = current_zone["zone_name"] if current_zone else None
                zone_type = current_zone["zone_type"] if current_zone else None
                is_revenue = current_zone["is_revenue_zone"] if current_zone else None
                
                prev_zone_id = info["zone_id"]
                
                if zone_id != prev_zone_id:
                    if prev_zone_id:
                        self.emit_event("zone_exited", t_id, {
                            "zone_id": prev_zone_id,
                            "wait_seconds": random.randint(10, 45),
                            "is_staff": info["is_staff"]
                        })
                    if zone_id:
                        info["zone_id"] = zone_id
                        self.emit_event("zone_entered", t_id, {
                            "zone_id": zone_id,
                            "zone_name": zone_name,
                            "zone_type": zone_type,
                            "is_revenue_zone": is_revenue,
                            "zone_hotspot_x": info["x"] * 1000.0,
                            "zone_hotspot_y": info["y"] * 1000.0,
                            "is_staff": info["is_staff"]
                        })
                        
                        # Handle queue entry for billing
                        if zone_type == "BILLING" and not info["is_staff"]:
                            info["joined_queue"] = datetime.utcnow()
                else:
                    self.emit_event("zone_update", t_id, {
                        "zone_hotspot_x": info["x"] * 1000.0,
                        "zone_hotspot_y": info["y"] * 1000.0,
                        "is_staff": info["is_staff"]
                    })
                    
            for t_id in finished_tracks:
                info = sim_tracks[t_id]
                
                # If they were in queue, emit queue completed or abandoned
                if info["joined_queue"] and not info["is_staff"]:
                    q_exit = datetime.utcnow()
                    wait_sec = (q_exit - info["joined_queue"]).total_seconds()
                    abandoned = random.random() < 0.15 # 15% abandon rate
                    
                    event_type = "queue_abandoned" if abandoned else "queue_completed"
                    
                    self.emit_event(event_type, t_id, {
                        "zone_id": info["zone_id"],
                        "zone_name": "Billing Counter Queue",
                        "zone_type": "BILLING",
                        "queue_event_id": f"q_{uuid.uuid4().hex[:8]}" if not abandoned else None,
                        "queue_join_ts": info["joined_queue"].isoformat(),
                        "queue_served_ts": (info["joined_queue"] + timedelta(seconds=wait_sec*0.4)).isoformat() if not abandoned else None,
                        "queue_exit_ts": q_exit.isoformat(),
                        "wait_seconds": int(wait_sec),
                        "queue_position_at_join": random.randint(1, 4),
                        "abandoned": abandoned,
                        "gender": info["gender"],
                        "age": info["age"]
                    })
                    
                self.emit_event("exit", t_id, {
                    "is_staff": info["is_staff"],
                    "zone_hotspot_x": info["x"] * 1000.0,
                    "zone_hotspot_y": info["y"] * 1000.0
                })
                del sim_tracks[t_id]
                
            await asyncio.sleep(5)
