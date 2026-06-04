#!/usr/bin/env python3
"""
Purplle Store Intelligence — Batch Video Processor
===================================================
Processes all store camera videos with YOLOv11 + ByteTrack.
Outputs annotated videos, per-camera event JSONLs, and store summaries.

Usage (run from  d:\\purpell\\v2\\store-intelligence\\pipeline  directory):
    python process_videos.py                          # All stores
    python process_videos.py --store ST1076           # One store only
    python process_videos.py --api http://localhost:8000  # Also post events live
    python process_videos.py --skip 2                 # Infer every 2nd frame (CPU mode)

Outputs:
    data/output/ST1076/CAM1_annotated.mp4
    data/output/ST1076/CAM1_events.jsonl
    data/output/ST1076/summary.json
    data/output/processing.log
"""

import os
import sys
import json
import time
import logging
import argparse
import numpy as np
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict, deque

# ── make tracker importable when run from pipeline/ directory ──────────────
_THIS_DIR = Path(__file__).parent
sys.path.insert(0, str(_THIS_DIR))

import cv2
import requests
import yaml
from tracker.group_detector import GroupDetector
from tracker.face_analyzer  import FaceAnalyzer

# ── Logging (file + console) ───────────────────────────────────────────────
_OUT_ROOT = _THIS_DIR / "data" / "output"
_OUT_ROOT.mkdir(parents=True, exist_ok=True)

# Force UTF-8 on the stream handler so Windows cp1252 never chokes on non-ASCII
_stream_handler = logging.StreamHandler(sys.stdout)
_stream_handler.stream.reconfigure(encoding="utf-8", errors="replace")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        _stream_handler,
        logging.FileHandler(str(_OUT_ROOT / "processing.log"), mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ── YOLO ───────────────────────────────────────────────────────────────────
try:
    from ultralytics import YOLO
    HAS_YOLO = True
except ImportError:
    logger.error("ultralytics not found. Run:  pip install ultralytics")
    sys.exit(1)

# ── PyTorch Re-ID ──────────────────────────────────────────────────────────
HAS_TORCH   = False
_REID_MODEL = None
_REID_DEV   = "cpu"
_REID_PRE   = None

try:
    import torch
    import torchvision.transforms as T
    from torchvision.models import mobilenet_v3_small, MobileNet_V3_Small_Weights

    _REID_DEV = "cuda" if torch.cuda.is_available() else "cpu"
    _w = MobileNet_V3_Small_Weights.DEFAULT
    _REID_MODEL = mobilenet_v3_small(weights=_w).eval().to(_REID_DEV)
    _REID_MODEL.classifier = torch.nn.Identity()          # output dim: 576
    _REID_PRE = T.Compose([
        T.ToPILImage(), T.Resize((128, 64)), T.ToTensor(),
        T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
    HAS_TORCH = True
    logger.info(f"MobileNetV3 Re-ID loaded on {_REID_DEV}.")
except Exception as _e:
    logger.warning(f"PyTorch unavailable ({_e}). Using HSV-histogram Re-ID.")


# ══════════════════════════════════════════════════════════════════════════
#  ZONE CONFIGS  (mirrors database.py seeds — normalized [x, y] coords)
# ══════════════════════════════════════════════════════════════════════════
ZONES: Dict[str, List[Dict]] = {
    "ST1076": [
        {"id": "Z01", "name": "Left Wall Shelves",      "type": "SHELF",    "revenue": True,
         "poly": [[0.00,0.00],[0.38,0.00],[0.38,0.25],[0.00,0.25]]},
        {"id": "Z02", "name": "Right Wall Shelves",     "type": "SHELF",    "revenue": True,
         "poly": [[0.52,0.00],[1.00,0.00],[1.00,0.25],[0.52,0.25]]},
        {"id": "Z03", "name": "F.O.H Center",           "type": "DISPLAY",  "revenue": True,
         "poly": [[0.30,0.30],[0.55,0.30],[0.55,0.65],[0.30,0.65]]},
        {"id": "Z04", "name": "Makeup Unit Center",     "type": "DISPLAY",  "revenue": True,
         "poly": [[0.52,0.30],[0.75,0.30],[0.75,0.65],[0.52,0.65]]},
        {"id": "Z05", "name": "Bottom Wall",            "type": "SHELF",    "revenue": True,
         "poly": [[0.10,0.75],[0.95,0.75],[0.95,1.00],[0.10,1.00]]},
        {"id": "Z06", "name": "Billing Counter",        "type": "BILLING",  "revenue": True,
         "poly": [[0.82,0.25],[1.00,0.25],[1.00,0.75],[0.82,0.75]]},
        {"id": "Z07", "name": "Entrance Corridor",      "type": "ENTRANCE", "revenue": False,
         "poly": [[0.00,0.30],[0.18,0.30],[0.18,0.70],[0.00,0.70]]},
    ],
    "ST1008": [
        {"id": "Z01", "name": "Left Wall Units",        "type": "SHELF",    "revenue": True,
         "poly": [[0.00,0.35],[0.12,0.35],[0.12,1.00],[0.00,1.00]]},
        {"id": "Z02", "name": "Top Wall Units",         "type": "SHELF",    "revenue": True,
         "poly": [[0.00,0.35],[1.00,0.35],[1.00,0.48],[0.00,0.48]]},
        {"id": "Z03", "name": "Right Wall Units",       "type": "SHELF",    "revenue": True,
         "poly": [[0.88,0.35],[1.00,0.35],[1.00,1.00],[0.88,1.00]]},
        {"id": "Z04", "name": "MK-Gondola Displays",   "type": "DISPLAY",  "revenue": True,
         "poly": [[0.15,0.55],[0.45,0.55],[0.45,0.95],[0.15,0.95]]},
        {"id": "Z05", "name": "Makeup Units",           "type": "DISPLAY",  "revenue": True,
         "poly": [[0.58,0.60],[0.85,0.60],[0.85,0.90],[0.58,0.90]]},
        {"id": "Z06", "name": "Billing Counter",        "type": "BILLING",  "revenue": True,
         "poly": [[0.38,0.42],[0.62,0.42],[0.62,0.58],[0.38,0.58]]},
        {"id": "Z07", "name": "Main Entrance",          "type": "ENTRANCE", "revenue": False,
         "poly": [[0.30,0.90],[0.70,0.90],[0.70,1.00],[0.30,1.00]]},
    ],
}

STORE_NAMES = {
    "ST1076": "Mumbai Central",
    "ST1008": "Delhi SelectCitywalk",
}

# Zone overlay colors (BGR)
ZONE_CLR = {
    "ENTRANCE": (200, 200,  30),   # cyan-yellow
    "SHELF":    (210, 105,  30),   # cornflower blue
    "DISPLAY":  ( 30, 165, 255),   # orange
    "BILLING":  ( 30,  60, 220),   # red
}

# Track annotation colors (BGR)
CLR_CUSTOMER = (  0, 220,  50)
CLR_STAFF    = (  0,  60, 220)
CLR_QUEUE    = (  0, 140, 255)

FONT = cv2.FONT_HERSHEY_SIMPLEX


# ══════════════════════════════════════════════════════════════════════════
#  GEOMETRY HELPERS
# ══════════════════════════════════════════════════════════════════════════
def _in_poly(px: float, py: float, poly: List[List[float]]) -> bool:
    """Ray-casting point-in-polygon for normalized coords."""
    inside = False
    n = len(poly)
    x1, y1 = poly[0]
    for i in range(n + 1):
        x2, y2 = poly[i % n]
        if py > min(y1, y2):
            if py <= max(y1, y2):
                if px <= max(x1, x2):
                    if y1 != y2:
                        xi = (py - y1) * (x2 - x1) / (y2 - y1) + x1
                    if x1 == x2 or px <= xi:
                        inside = not inside
        x1, y1 = x2, y2
    return inside


def get_zone(px: float, py: float, zones: List[Dict]) -> Optional[Dict]:
    for z in zones:
        if _in_poly(px, py, z["poly"]):
            return z
    return None


# ══════════════════════════════════════════════════════════════════════════
#  RE-ID TRACKER  (synchronous, local memory only)
# ══════════════════════════════════════════════════════════════════════════
class ReIDTracker:
    def __init__(self, threshold: float = 0.82, ttl: int = 300):
        self.threshold = threshold
        self.ttl       = ttl
        # key = "{store_id}:{track_id}" → (track_id, embedding, timestamp)
        self._buf: Dict[str, Tuple[str, np.ndarray, float]] = {}

    def _embed(self, crop: np.ndarray) -> np.ndarray:
        if crop is None or crop.size == 0:
            return np.zeros((128,), dtype=np.float32)

        if HAS_TORCH and _REID_MODEL is not None:
            try:
                rgb = crop[:, :, ::-1].copy()
                t   = _REID_PRE(rgb).unsqueeze(0).to(_REID_DEV)
                with torch.no_grad():
                    e = _REID_MODEL(t).squeeze().cpu().numpy()
                n = np.linalg.norm(e)
                return (e / n).astype(np.float32) if n > 0 else e.astype(np.float32)
            except Exception:
                pass

        # HSV histogram fallback
        try:
            hsv  = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1, 2], None, [8, 8, 8],
                                [0, 180, 0, 256, 0, 256])
            cv2.normalize(hist, hist)
            return hist.flatten().astype(np.float32)
        except Exception:
            return np.zeros((512,), dtype=np.float32)

    def match_or_register(self, store_id: str, crop: np.ndarray, tid: str) -> str:
        now = time.time()
        # Prune expired
        expired = [k for k, v in self._buf.items() if now - v[2] > self.ttl]
        for k in expired:
            del self._buf[k]

        emb = self._embed(crop)
        if np.linalg.norm(emb) == 0:
            return tid

        prefix     = f"{store_id}:"
        best_id, best_sim = None, -1.0
        for key, (ref_tid, ref_emb, _) in self._buf.items():
            if not key.startswith(prefix):
                continue
            # Cosine sim (both unit vectors after _embed)
            try:
                sim = float(np.dot(emb, ref_emb) /
                            (np.linalg.norm(emb) * np.linalg.norm(ref_emb) + 1e-9))
            except Exception:
                continue
            if sim > best_sim:
                best_sim = sim
                best_id  = ref_tid

        if best_id and best_sim >= self.threshold:
            self._buf[f"{store_id}:{best_id}"] = (best_id, emb, now)
            return best_id

        self._buf[f"{store_id}:{tid}"] = (tid, emb, now)
        return tid


# ══════════════════════════════════════════════════════════════════════════
#  STAFF FILTER  (precision composite-score version)
# ══════════════════════════════════════════════════════════════════════════
# Uniform specs — tuned per store to minimise customer false-positives
_UNIFORM_SPEC = {
    "ST1076": {  # All-black uniform: low V AND low S
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
_STAFF_SCORE_THRESH  = 2.0   # composite score needed to flag
_UNIFORM_SCORE       = 2.0   # decisive on its own
_VEL_SCORE_PER_FRAME = 0.6
_REVISIT_SCORE_EXTRA = 0.5
_VEL_THRESHOLD       = 0.25  # normalised coords / sec (was 0.18)
_VEL_MIN_STREAK      = 3     # consecutive high-vel frames required
_REVISIT_THRESH_DEF  = 4     # zone revisits before suspicion (was 3)


class StaffFilter:
    """
    Detects staff via composite confidence score.
    Three signals — uniform colour, sustained high velocity, zone over-revisit —
    each contribute to a score; no single signal alone crosses the threshold.
    Customers confirmed by Re-ID can be locked to prevent reclassification.
    """
    def __init__(self, store_id: str = "ST1008",
                 vel_thresh: float = _VEL_THRESHOLD,
                 revisit_thresh: int = _REVISIT_THRESH_DEF):
        self.store_id        = store_id
        self.vel_thresh      = vel_thresh
        self.revisit_thresh  = revisit_thresh
        self._staff:    set  = set()
        self._customers: set = set()          # Re-ID confirmed customers
        self._score: Dict[str, float]        = defaultdict(float)
        self._pos:   Dict[str, deque]        = defaultdict(lambda: deque(maxlen=15))
        self._vel_streak: Dict[str, int]     = defaultdict(int)
        self._zone_visits: Dict[str, Dict]   = defaultdict(lambda: defaultdict(int))

    # ── Predicates ────────────────────────────────────────────────────────
    def is_staff(self, tid: str) -> bool:
        return tid in self._staff

    def flag(self, tid: str):
        """Force-flag as staff (only if not customer-locked)."""
        if tid not in self._customers:
            self._staff.add(tid)

    def lock_as_customer(self, tid: str):
        """Mark this id_token as a confirmed customer; blocks reclassification."""
        self._customers.add(tid)
        self._staff.discard(tid)

    # ── Signals ───────────────────────────────────────────────────────────
    def check_uniform(self, crop: np.ndarray, bbox_h: int = 0) -> bool:
        """
        Returns True and bumps composite score if torso colour matches
        the store-specific staff uniform with sufficient coverage.
        Crop must represent a person bounding box (≥ 60 px tall).
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
                logger.debug(
                    f"Uniform match ({spec['label']} ratio={ratio:.3f}) for potential staff"
                )
                return True
        except Exception:
            pass
        return False

    def apply_uniform_score(self, tid: str) -> bool:
        """Add UNIFORM_SCORE for tid and check for promotion. Returns is_staff."""
        if tid in self._customers:
            return False
        if tid in self._staff:
            return True
        self._score[tid] += _UNIFORM_SCORE
        return self._promote(tid)

    def update_velocity(self, tid: str, x: float, y: float, t: float) -> bool:
        """Track velocity; requires _VEL_MIN_STREAK high-vel frames to contribute."""
        if tid in self._customers:
            return False
        if tid in self._staff:
            return True
        h = self._pos[tid]
        h.append((t, x, y))
        if len(h) >= 3:
            dt = h[-1][0] - h[0][0]
            if dt > 0.05:
                dx = h[-1][1] - h[0][1]
                dy = h[-1][2] - h[0][2]
                speed = (dx**2 + dy**2) ** 0.5 / dt
                if speed > self.vel_thresh:
                    self._vel_streak[tid] += 1
                    if self._vel_streak[tid] >= _VEL_MIN_STREAK:
                        self._score[tid] += _VEL_SCORE_PER_FRAME
                        return self._promote(tid)
                else:
                    self._vel_streak[tid] = max(0, self._vel_streak[tid] - 1)
        return False

    def record_zone(self, tid: str, zone_id: str) -> bool:
        """Record zone entry; excess revisits add to composite score."""
        if tid in self._customers:
            return False
        if tid in self._staff:
            return True
        self._zone_visits[tid][zone_id] += 1
        if self._zone_visits[tid][zone_id] > self.revisit_thresh:
            self._score[tid] += _REVISIT_SCORE_EXTRA
            return self._promote(tid)
        return False

    def _promote(self, tid: str) -> bool:
        if tid in self._customers:
            return False
        if self._score[tid] >= _STAFF_SCORE_THRESH:
            self._staff.add(tid)
            logger.info(f"[StaffFilter] Promoted {tid} to STAFF (score={self._score[tid]:.1f})")
            return True
        return False


# ══════════════════════════════════════════════════════════════════════════
#  FRAME ANNOTATOR
# ══════════════════════════════════════════════════════════════════════════
class Annotator:
    def __init__(self, store_id: str, cam_id: str, cam_role: str, zones: List[Dict]):
        self.store_id  = store_id
        self.cam_id    = cam_id
        self.cam_role  = cam_role
        self.zones     = zones
        self._store_name = STORE_NAMES.get(store_id, store_id)

    def draw(self, frame: np.ndarray, tracks: Dict,
             frame_idx: int, fps: float, n_events: int) -> np.ndarray:
        h, w = frame.shape[:2]
        out  = frame.copy()

        # ── 3. Person bounding boxes ───────────────────────────────────────
        n_cust = n_staff = n_queue = 0
        for tid, info in tracks.items():
            x1, y1, x2, y2 = info["bbox"]
            is_staff   = info.get("is_staff", False)
            zone_type  = info.get("zone_type", "")

            if is_staff:
                color = CLR_STAFF;    label = f"STAFF {tid}"; n_staff += 1
            elif zone_type == "BILLING":
                color = CLR_QUEUE;   label = f"Q:{tid}";      n_queue += 1
            else:
                color = CLR_CUSTOMER; label = f"{tid}";       n_cust  += 1

            # Append Group ID if available
            group_id = info.get("group_id")
            if group_id:
                label += f" [G]"

            cv2.rectangle(out, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)
            # Label pill
            (lw, lh), _ = cv2.getTextSize(label, FONT, 0.44, 1)
            cv2.rectangle(out, (x1, y1 - lh - 8), (x1 + lw + 6, y1), color, -1)
            cv2.putText(out, label, (x1 + 3, y1 - 4), FONT, 0.44, (0, 0, 0), 1, cv2.LINE_AA)

        # ── 4. Top HUD bar ─────────────────────────────────────────────────
        cv2.rectangle(out, (0, 0), (w, 44), (15, 15, 15), -1)
        elapsed_s = frame_idx / max(fps, 1)
        ts_str    = f"{int(elapsed_s // 60):02d}:{int(elapsed_s % 60):02d}"
        hdr = (f"PURPLLE INTEL  |  {self._store_name} ({self.store_id})"
               f"  |  {self.cam_id} — {self.cam_role.upper()}  |  T+{ts_str}")
        cv2.putText(out, hdr, (10, 29), FONT, 0.50, (200, 200, 200), 1, cv2.LINE_AA)

        # ── 5. Bottom HUD bar ──────────────────────────────────────────────
        cv2.rectangle(out, (0, h - 34), (w, h), (15, 15, 15), -1)
        ftr = (f"Customers: {n_cust}   Staff: {n_staff}   Queue: {n_queue}"
               f"   Events Emitted: {n_events}   Frame: {frame_idx}")
        cv2.putText(out, ftr, (10, h - 11), FONT, 0.41, (170, 170, 170), 1, cv2.LINE_AA)

        # ── 6. Live pulse dot (top-right) ──────────────────────────────────
        r_val = int(abs(np.sin(frame_idx * 0.06)) * 128) + 127
        cv2.circle(out, (w - 22, 22), 8, (0, 0, r_val), -1, cv2.LINE_AA)
        cv2.putText(out, "REC", (w - 60, 28), FONT, 0.38, (0, 0, 200), 1, cv2.LINE_AA)

        return out


# ══════════════════════════════════════════════════════════════════════════
#  SINGLE-CAMERA PROCESSOR
# ══════════════════════════════════════════════════════════════════════════
class CameraProcessor:
    """
    Processes a single camera video feed.

    Key additions vs. original:
    - Billing-dwell tracker: non-staff in BILLING zone ≥ 10 s → billed_customer event
    - Re-entry guard: person returning within 10 min gets re_entry event, same id_token
    - Schema-aligned _emit(): fields match sample_events.jsonl
    - Customer lock: after Re-ID match, track is locked as customer in StaffFilter
    """
    # Billing dwell threshold in video-time seconds
    BILLING_DWELL_SECS = 10.0
    # Re-entry window: treat same person returning within this many seconds as re-entry
    REENTRY_WINDOW_SECS = 600.0   # 10 minutes

    def __init__(self,
                 store_id:   str,
                 cam_id:     str,
                 cam_role:   str,
                 video_path: str,
                 output_dir: Path,
                 zones:      List[Dict],
                 model:      Any,
                 reid:       ReIDTracker,
                 staff:      StaffFilter,
                 group_detector: GroupDetector,
                 face_analyzer: FaceAnalyzer,
                 api_url:    Optional[str] = None,
                 skip:       int = 1):

        self.store_id   = store_id
        self.cam_id     = cam_id
        self.cam_role   = cam_role
        self.video_path = video_path
        self.output_dir = output_dir
        self.zones      = zones
        self.model      = model
        self.reid       = reid
        self.staff      = staff
        self.group_detector = group_detector
        self.face_analyzer  = face_analyzer
        self.api_url    = api_url
        self.skip       = max(1, skip)

        # Formatted store_code for schema alignment (e.g. "store_1076")
        self.store_code = f"store_{store_id.lstrip('ST').lstrip('0') or store_id}"

        self.annotator = Annotator(store_id, cam_id, cam_role, zones)
        self.events: List[Dict] = []
        self.active: Dict[str, Dict] = {}       # id_token → track state
        # Per-track latest face analysis: id_token → face result dict
        self._face_cache: Dict[str, Dict] = {}

        # Billing-dwell: id_token → video-ts when first entered billing zone
        self._billing_enter: Dict[str, float] = {}
        # Tracks that already got a billed_customer event this visit
        self._billed_this_visit: set = set()

        # Re-entry guard: id_token → video-ts when they exited
        self._exited_at: Dict[str, float] = {}

    # ── Event helper ──────────────────────────────────────────────────────
    def _emit(self, etype: str, tid: str, extra: Dict):
        """
        Emit a single event.  Field names match sample_events.jsonl schema:
          id_token, store_code, camera_id, event_timestamp, is_staff,
          gender_pred, age_pred, age_bucket, is_face_hidden, group_id, group_size
        Face fields are pulled from the per-track cache populated by FaceAnalyzer.
        """
        face = self._face_cache.get(tid, {})
        ev = {
            "event_type":       etype,
            "id_token":         tid,
            "store_code":       self.store_code,
            "store_id":         self.store_id,          # keep for internal use
            "camera_id":        self.cam_id,
            "event_timestamp":  datetime.now(timezone.utc).isoformat(),
            # Demographic fields: from FaceAnalyzer cache, overrideable via extra
            "gender_pred":      extra.pop("gender_pred", face.get("gender_pred")),
            "age_pred":         extra.pop("age_pred",    face.get("age_pred")),
            "age_bucket":       extra.pop("age_bucket",  face.get("age_bucket")),
            "is_face_hidden":   extra.pop("is_face_hidden",
                                          face.get("is_face_hidden", False)),
            "group_id":         extra.pop("group_id", None),
            "group_size":       extra.pop("group_size", None),
            **extra,
        }
        self.events.append(ev)
        if self.api_url:
            try:
                requests.post(f"{self.api_url}/api/v1/events", json=ev, timeout=1.5)
            except Exception:
                pass

    # ── Billing-dwell helpers ─────────────────────────────────────────────
    def _update_billing_dwell(self, id_token: str, zone_type: Optional[str],
                               ts: float, is_staff: bool):
        """Track dwell time in BILLING zone; emit billed_customer after 10 s."""
        if is_staff:
            # Staff at billing counter — reset any dwell state
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
                    self._emit("billed_customer", id_token, {
                        "zone_id":       self.active.get(id_token, {}).get("zone_id"),
                        "zone_name":     "Billing Counter",
                        "zone_type":     "BILLING",
                        "dwell_seconds": round(dwell, 1),
                        "is_staff":      False,
                    })
                    logger.info(
                        f"[{self.cam_id}] billed_customer emitted: {id_token} "
                        f"(dwell={dwell:.1f}s)"
                    )
        else:
            # Left billing zone — reset dwell and allow re-trigger on next visit
            if id_token in self._billing_enter:
                self._billing_enter.pop(id_token, None)
                self._billed_this_visit.discard(id_token)

    # ── Main processing loop ───────────────────────────────────────────────
    def process(self) -> Dict:
        if not os.path.exists(self.video_path):
            logger.error(f"[{self.cam_id}] Video not found: {self.video_path}")
            return {}

        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            logger.error(f"[{self.cam_id}] Cannot open: {self.video_path}")
            return {}

        fps   = cap.get(cv2.CAP_PROP_FPS) or 25.0
        n_tot = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        W     = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        H     = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        dur_m = n_tot / fps / 60

        logger.info(
            f"[{self.cam_id}] {W}×{H} @ {fps:.1f} fps | "
            f"{n_tot} frames ({dur_m:.1f} min) | role={self.cam_role} | skip={self.skip}"
        )

        self.output_dir.mkdir(parents=True, exist_ok=True)
        out_video  = str(self.output_dir / f"{self.cam_id}_annotated.mp4")
        out_events = self.output_dir / f"{self.cam_id}_events.jsonl"

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(out_video, fourcc, fps, (W, H))
        if not writer.isOpened():
            logger.error(f"[{self.cam_id}] Cannot create VideoWriter: {out_video}")
            cap.release()
            return {}

        frame_idx = 0
        last_annotations: Dict[str, Dict] = {}
        t0 = time.time()

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            frame_idx += 1
            ts = frame_idx / fps   # video-time seconds

            # ── YOLO inference (every `skip` frames) ──────────────────────
            if frame_idx % self.skip == 0:
                try:
                    results = self.model.track(
                        frame, persist=True, classes=[0], verbose=False
                    )
                    last_annotations = {}

                    if results and results[0].boxes is not None:
                        boxes    = results[0].boxes
                        tids_raw = boxes.id
                        xyxys    = boxes.xyxy.cpu().numpy()

                        if tids_raw is not None:
                            tids_np = tids_raw.cpu().numpy().astype(int)
                            visible = set()

                            for i, raw_t in enumerate(tids_np):
                                raw_id = f"T{raw_t}"
                                x1, y1, x2, y2 = (int(v) for v in xyxys[i])
                                x1 = max(0, x1); y1 = max(0, y1)
                                x2 = min(W, x2); y2 = min(H, y2)
                                cx = (x1 + x2) / (2.0 * W)
                                cy = (y1 + y2) / (2.0 * H)
                                bbox_h = y2 - y1

                                crop = frame[y1:y2, x1:x2]

                                # ── Staff detection (composite score) ─────────
                                is_staff = self.staff.is_staff(raw_id)
                                if not is_staff:
                                    uniform_hit = self.staff.check_uniform(
                                        crop, bbox_h=bbox_h
                                    )
                                    if uniform_hit:
                                        is_staff = self.staff.apply_uniform_score(raw_id)
                                    if not is_staff:
                                        is_staff = self.staff.update_velocity(
                                            raw_id, cx, cy, ts
                                        )

                                # ── Face analysis (every 15 inferred frames) ───
                                # Only run for non-staff to avoid wasting cycles;
                                # update cache only when a better result is found.
                                if not is_staff:
                                    _face_interval = max(1, 15 // self.skip)
                                    if (frame_idx // self.skip) % _face_interval == 0:
                                        face_result = self.face_analyzer.analyze(crop)
                                        cached = self._face_cache.get(raw_id, {})
                                        # Prefer non-null over null (keep first good result)
                                        if face_result.get("gender_pred") is not None:
                                            self._face_cache[raw_id] = face_result
                                        elif raw_id not in self._face_cache:
                                            self._face_cache[raw_id] = face_result

                                # ── Re-ID (customers only) ─────────────────────
                                id_token = raw_id
                                if not is_staff:
                                    id_token = self.reid.match_or_register(
                                        self.store_id, crop, raw_id
                                    )
                                    # Lock confirmed customer to prevent reclassification
                                    self.staff.lock_as_customer(id_token)
                                    # Propagate face cache: if Re-ID merged raw_id → id_token
                                    if id_token != raw_id and raw_id in self._face_cache:
                                        if id_token not in self._face_cache:
                                            self._face_cache[id_token] = self._face_cache.pop(raw_id)
                                        else:
                                            # Keep the richer (non-null) result
                                            existing = self._face_cache[id_token]
                                            candidate = self._face_cache[raw_id]
                                            if (existing.get("gender_pred") is None and
                                                    candidate.get("gender_pred") is not None):
                                                self._face_cache[id_token] = candidate
                                            self._face_cache.pop(raw_id, None)

                                # ── Zone lookup ────────────────────────────────
                                zone      = get_zone(cx, cy, self.zones)
                                zone_id   = zone["id"]   if zone else None
                                zone_name = zone["name"] if zone else None
                                zone_type = zone["type"] if zone else None

                                # ── Billing dwell check ────────────────────────
                                self._update_billing_dwell(
                                    id_token, zone_type, ts, is_staff
                                )

                                # ── State machine ──────────────────────────────
                                prev = self.active.get(id_token)

                                if prev is None:
                                    # ── Re-entry guard ────────────────────────
                                    exit_ts = self._exited_at.get(id_token)
                                    if exit_ts is not None and (ts - exit_ts) <= self.REENTRY_WINDOW_SECS:
                                        # Same person returning — emit re_entry
                                        self._emit("re_entry", id_token, {
                                            "is_staff":       is_staff,
                                            "gap_seconds":    round(ts - exit_ts, 1),
                                            "zone_hotspot_x": cx * 1000,
                                            "zone_hotspot_y": cy * 1000,
                                        })
                                        logger.info(
                                            f"[{self.cam_id}] Re-entry detected: {id_token} "
                                            f"(gap={ts-exit_ts:.1f}s)"
                                        )
                                    else:
                                        # Genuine new entry
                                        self._emit("entry", id_token, {
                                            "is_staff":       is_staff,
                                            "zone_hotspot_x": cx * 1000,
                                            "zone_hotspot_y": cy * 1000,
                                        })

                                    self.active[id_token] = {
                                        "zone_id":      zone_id,
                                        "zone_type":    zone_type,
                                        "entered_time": ts,
                                        "last_seen":    ts,
                                        "is_staff":     is_staff,
                                        "x": cx, "y": cy,
                                    }
                                    if zone_id:
                                        self._emit("zone_entered", id_token, {
                                            "zone_id":         zone_id,
                                            "zone_name":       zone_name,
                                            "zone_type":       zone_type,
                                            "is_revenue_zone": zone["revenue"] if zone else False,
                                            "is_staff":        is_staff,
                                            "zone_hotspot_x":  cx * 1000,
                                            "zone_hotspot_y":  cy * 1000,
                                        })
                                else:
                                    prev["last_seen"] = ts
                                    prev["x"] = cx
                                    prev["y"] = cy

                                    if zone_id != prev["zone_id"]:
                                        # Zone transition
                                        if prev["zone_id"]:
                                            self._emit("zone_exited", id_token, {
                                                "zone_id":      prev["zone_id"],
                                                "wait_seconds": int(ts - prev["entered_time"]),
                                                "is_staff":     is_staff,
                                            })
                                            if zone_id and self.staff.record_zone(
                                                id_token, zone_id
                                            ):
                                                is_staff = True
                                                prev["is_staff"] = True

                                        prev["zone_id"]      = zone_id
                                        prev["zone_type"]    = zone_type
                                        prev["entered_time"] = ts
                                        prev["is_staff"]     = is_staff

                                        if zone_id:
                                            self._emit("zone_entered", id_token, {
                                                "zone_id":         zone_id,
                                                "zone_name":       zone_name,
                                                "zone_type":       zone_type,
                                                "is_revenue_zone": zone["revenue"] if zone else False,
                                                "is_staff":        is_staff,
                                                "zone_hotspot_x":  cx * 1000,
                                                "zone_hotspot_y":  cy * 1000,
                                            })
                                    else:
                                        self._emit("zone_update", id_token, {
                                            "zone_hotspot_x": cx * 1000,
                                            "zone_hotspot_y": cy * 1000,
                                            "is_staff":       is_staff,
                                        })

                                visible.add(id_token)
                                last_annotations[id_token] = {
                                    "bbox":      (x1, y1, x2, y2),
                                    "is_staff":  self.staff.is_staff(id_token),
                                    "zone_type": zone_type,
                                }

                            # ── Expire lost tracks (≥ 3 video-seconds gone) ───
                            lost = [
                                t for t, s in self.active.items()
                                if ts - s["last_seen"] > 3.0 and t not in visible
                            ]
                            for t in lost:
                                s = self.active.pop(t)
                                self._exited_at[t] = ts      # record exit for re-entry guard
                                self._face_cache.pop(t, None)  # clean up face cache
                                self._emit("exit", t, {
                                    "is_staff":       s["is_staff"],
                                    "zone_hotspot_x": s["x"] * 1000,
                                    "zone_hotspot_y": s["y"] * 1000,
                                })
                                last_annotations.pop(t, None)
                                # Clean up billing dwell state
                                self._billing_enter.pop(t, None)
                                self._billed_this_visit.discard(t)

                            # ── Grouping ──────────────────────────────────────
                            groups = self.group_detector.update_groups(self.active, ts)
                            for t, ann in last_annotations.items():
                                if t in groups:
                                    ann["group_id"] = groups[t]

                except Exception as exc:
                    logger.error(f"[{self.cam_id}] Frame {frame_idx} error: {exc}")

            # ── Annotate & write every frame ──────────────────────────────
            ann = self.annotator.draw(frame, last_annotations, frame_idx, fps, len(self.events))
            writer.write(ann)

            # ── Progress log every 300 frames ─────────────────────────────
            if frame_idx % 300 == 0:
                elapsed = time.time() - t0
                pct  = frame_idx / n_tot * 100 if n_tot > 0 else 0
                pfps = frame_idx / max(elapsed, 0.01)
                eta  = (n_tot - frame_idx) / pfps / 60 if pfps > 0 else 0
                logger.info(
                    f"  [{self.cam_id}] {pct:5.1f}%  "
                    f"{pfps:.1f} proc-fps  ETA {eta:.1f} min  "
                    f"tracks={len(self.active)}  events={len(self.events)}"
                )

        # ── Final exits for remaining active tracks ────────────────────────
        for tid, s in list(self.active.items()):
            self._exited_at[tid] = ts if frame_idx else 0
            self._emit("exit", tid, {
                "is_staff":       s["is_staff"],
                "zone_hotspot_x": s["x"] * 1000,
                "zone_hotspot_y": s["y"] * 1000,
            })

        cap.release()
        writer.release()

        # Transcode to H.264 for browser compatibility
        temp_out = out_video.replace(".mp4", "_temp.mp4")
        try:
            import subprocess
            import imageio_ffmpeg
            ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
            if os.path.exists(out_video):
                os.rename(out_video, temp_out)
                logger.info(f"[{self.cam_id}] Transcoding to H.264…")
                subprocess.run(
                    [ffmpeg_bin, "-y", "-i", temp_out,
                     "-c:v", "libx264", "-pix_fmt", "yuv420p",
                     "-movflags", "+faststart", out_video],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
                )
                os.remove(temp_out)
                logger.info(f"[{self.cam_id}] Transcode complete.")
        except Exception as e:
            logger.warning(f"[{self.cam_id}] Transcode failed ({e}). Raw mp4v kept.")
            if os.path.exists(temp_out) and not os.path.exists(out_video):
                os.rename(temp_out, out_video)

        # ── Write per-camera events JSONL ──────────────────────────────────
        with open(out_events, "w", encoding="utf-8") as f:
            for ev in self.events:
                f.write(json.dumps(ev) + "\n")

        wall = time.time() - t0
        billed = sum(1 for e in self.events if e["event_type"] == "billed_customer")
        reentry = sum(1 for e in self.events if e["event_type"] == "re_entry")
        logger.info(
            f"[{self.cam_id}] ✓ Finished  "
            f"{frame_idx} frames | {len(self.events)} events | "
            f"{wall/60:.1f} min wall-time | "
            f"billed_customer={billed} re_entry={reentry}"
        )
        logger.info(f"  Video  → {out_video}")
        logger.info(f"  Events → {out_events}")

        return {
            "cam_id":     self.cam_id,
            "cam_role":   self.cam_role,
            "frames":     frame_idx,
            "events":     len(self.events),
            "out_video":  out_video,
            "out_events": str(out_events),
            "wall_sec":   round(wall, 1),
            "customers":  sum(
                1 for e in self.events
                if e["event_type"] == "entry" and not e.get("is_staff")
            ),
            "staff": sum(
                1 for e in self.events
                if e["event_type"] == "entry" and e.get("is_staff")
            ),
            "billed_customers": billed,
            "re_entries":       reentry,
        }


# ══════════════════════════════════════════════════════════════════════════
#  ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════
def run(args):
    logger.info("=" * 65)
    logger.info("  Purplle Store Intelligence — Video Processor")
    logger.info(f"  Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 65)

    # Load camera config
    cfg_path = _THIS_DIR / "config" / "cameras.yaml"
    if not cfg_path.exists():
        logger.error(f"Config not found: {cfg_path}")
        sys.exit(1)
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)

    # Load YOLOv11n (downloads automatically on first use, ~6 MB)
    logger.info("Loading YOLOv11n model …")
    model  = YOLO("yolo11n.pt")
    reid   = ReIDTracker()

    total_start = time.time()
    all_results: Dict[str, List[Dict]] = {}

    for store_id, store_cfg in cfg.get("stores", {}).items():
        if args.store and args.store != store_id:
            continue

        cameras  = store_cfg.get("cameras", [])
        zones    = ZONES.get(store_id, [])
        out_dir  = _OUT_ROOT / store_id
        store_results = []

        logger.info("")
        logger.info(f"==  {STORE_NAMES.get(store_id, store_id)} ({store_id})  "
                    f"[{len(cameras)} cameras, {len(zones)} zones]  ==")

        # Shared staff filter per store (tracks are cross-camera for same store)
        staff = StaffFilter(store_id=store_id)
        group_detector = GroupDetector(time_threshold_seconds=10.0, distance_threshold=0.08)
        face_analyzer  = FaceAnalyzer(download_models=True)

        for cam in cameras:
            cam_id   = cam["id"]
            cam_role = cam.get("role", "zone")
            vid_file = cam["file"]           # relative to pipeline/
            vid_path = str(_THIS_DIR / vid_file)

            logger.info("")
            logger.info(f"  >> Camera {cam_id} ({cam_role}): {vid_file}")

            proc = CameraProcessor(
                store_id       = store_id,
                cam_id         = cam_id,
                cam_role       = cam_role,
                video_path     = vid_path,
                output_dir     = out_dir,
                zones          = zones,
                model          = model,
                reid           = reid,
                staff          = staff,
                group_detector = group_detector,
                face_analyzer  = face_analyzer,
                api_url        = args.api if not args.no_api else None,
                skip           = args.skip,
            )
            result = proc.process()
            if result:
                store_results.append(result)

        # ── Per-store summary ─────────────────────────────────────────────
        if store_results:
            summary = {
                "store_id":        store_id,
                "store_name":      STORE_NAMES.get(store_id, store_id),
                "processed_at":    datetime.now().isoformat(),
                "cameras":         store_results,
                "totals": {
                    "total_events":          sum(r["events"]           for r in store_results),
                    "total_customers":       sum(r["customers"]        for r in store_results),
                    "total_staff":           sum(r["staff"]            for r in store_results),
                    "total_billed":          sum(r.get("billed_customers", 0) for r in store_results),
                    "total_re_entries":      sum(r.get("re_entries", 0)       for r in store_results),
                    "wall_seconds":          round(sum(r["wall_sec"] for r in store_results), 1),
                },
            }
            summary_path = out_dir / "summary.json"
            with open(summary_path, "w") as f:
                json.dump(summary, f, indent=2)
            logger.info("")
            logger.info(f"  ✓ Store summary → {summary_path}")
            logger.info(
                f"    events={summary['totals']['total_events']}  "
                f"customers={summary['totals']['total_customers']}  "
                f"staff={summary['totals']['total_staff']}  "
                f"billed={summary['totals']['total_billed']}  "
                f"re_entries={summary['totals']['total_re_entries']}"
            )
            all_results[store_id] = store_results

            # ── Consolidated per-store JSONL (all cameras merged, sorted by timestamp) ──
            cam_event_files = [
                Path(r["out_events"]) for r in store_results
                if r.get("out_events") and Path(r["out_events"]).exists()
            ]
            if cam_event_files:
                merged_events = []
                for ef in cam_event_files:
                    try:
                        with open(ef, "r", encoding="utf-8") as fh:
                            for line in fh:
                                line = line.strip()
                                if line:
                                    merged_events.append(json.loads(line))
                    except Exception as merge_err:
                        logger.warning(f"  Could not read {ef} for merge: {merge_err}")

                # Sort chronologically by event_timestamp
                merged_events.sort(
                    key=lambda e: e.get("event_timestamp", e.get("timestamp", ""))
                )

                consolidated_path = out_dir / f"{store_id}_events.jsonl"
                with open(consolidated_path, "w", encoding="utf-8") as fh:
                    for ev in merged_events:
                        fh.write(json.dumps(ev) + "\n")

                logger.info(
                    f"  ✓ Consolidated events → {consolidated_path} "
                    f"({len(merged_events)} total events from {len(cam_event_files)} cameras)"
                )

    # ── Final report ──────────────────────────────────────────────────────
    total_wall = time.time() - total_start
    logger.info("")
    logger.info("=" * 65)
    logger.info(f"  ALL DONE in {total_wall/60:.1f} min")
    logger.info(f"  Outputs in: {_OUT_ROOT}")
    logger.info("=" * 65)


# ══════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Purplle Store Intelligence — batch video processor"
    )
    ap.add_argument("--store", default=None,
                    help="Process one store only: ST1076 or ST1008")
    ap.add_argument("--api", default="http://localhost:8000",
                    help="Backend URL to post events live (default: http://localhost:8000)")
    ap.add_argument("--no-api", action="store_true",
                    help="Disable live API posting (offline mode)")
    ap.add_argument("--skip", type=int, default=1,
                    help="Run YOLO every N frames (1=every frame GPU, 2-3=CPU mode)")
    args = ap.parse_args()
    run(args)
