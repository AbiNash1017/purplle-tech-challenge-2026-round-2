"""
tracker/staff_filter.py
=======================
Identifies store staff by combining three independent signals into a composite
confidence score.  No single signal alone can promote a person to "staff".

Uniform colours (store-specific, precision-tuned):
  ST1076 (Mumbai Central)       — All-black uniform:
      HSV: V < 50 AND S < 80, torso coverage ≥ 25 %
  ST1008 (Delhi SelectCitywalk) — Hot-pink/magenta uniform:
      HSV: Hue 150-170, S > 80, V > 60, torso coverage ≥ 15 %

Composite staff_score:
  uniform match   → +2.0   (strong signal)
  high velocity   → +0.6 per frame (accumulates, requires ≥ 3 frames > thresh)
  zone revisit    → +0.5 per excess revisit
  → Flagged as staff only when score ≥ 2.0

Customer whitelist:
  Once Re-ID has confirmed a stable customer id_token, calling
  lock_as_customer(tid) prevents that track from ever being reclassified.
"""

import numpy as np
import cv2
from collections import defaultdict, deque
from typing import Dict, Optional

# ── Per-store uniform specs ────────────────────────────────────────────────
_UNIFORM = {
    "ST1076": {
        # All-black: low brightness AND low saturation
        "lower":     np.array([0,   0,   0]),
        "upper":     np.array([180, 80,  50]),
        "ratio_min": 0.25,          # ≥ 25 % of torso must match
        "label":     "black",
    },
    "ST1008": {
        # Hot pink / magenta: narrow hue, high saturation + brightness
        "lower":     np.array([150, 80,  60]),
        "upper":     np.array([170, 255, 255]),
        "ratio_min": 0.15,          # ≥ 15 % of torso must match
        "label":     "pink/magenta",
    },
}

# Composite score thresholds
_STAFF_SCORE_THRESHOLD = 2.0
_UNIFORM_SCORE        = 2.0   # uniform match is decisive on its own
_VELOCITY_SCORE_FRAME = 0.6   # added per high-velocity frame
_REVISIT_SCORE_EXTRA  = 0.5   # added per excess zone revisit

# Velocity: must exceed threshold for at least this many frames
_VEL_THRESHOLD        = 0.25  # normalised coords / second
_VEL_MIN_FRAMES       = 3     # consecutive high-vel observations required


class StaffFilter:
    """
    Filters out staff using a composite confidence score.

    Public API
    ----------
    is_staff(tid)                → bool
    flag(tid)                    → None   (force-flag as staff)
    lock_as_customer(tid)        → None   (prevent reclassification)
    check_uniform(crop, bbox_h)  → bool
    update_velocity(tid, x, y, t)→ bool
    record_zone(tid, zone_id)    → bool
    """

    def __init__(self, store_id: str = "ST1008",
                 vel_thresh:    float = _VEL_THRESHOLD,
                 revisit_thresh: int  = 4):
        self.store_id        = store_id
        self.vel_thresh      = vel_thresh
        self.revisit_thresh  = revisit_thresh

        self._staff:    set = set()
        self._customers: set = set()           # locked customer tokens

        # Composite score accumulator
        self._score:  Dict[str, float] = defaultdict(float)

        # Position history for velocity: tid → deque of (t, x, y)
        self._pos:    Dict[str, deque]  = defaultdict(lambda: deque(maxlen=15))
        # Count of consecutive high-velocity frames per track
        self._vel_streak: Dict[str, int] = defaultdict(int)

        # Zone visit counts: tid → {zone_id: count}
        self._zone_visits: Dict[str, Dict] = defaultdict(lambda: defaultdict(int))

    # ── Public predicates ──────────────────────────────────────────────────

    def is_staff(self, tid: str) -> bool:
        return tid in self._staff

    def flag(self, tid: str):
        """Force-promote to staff (bypasses score check)."""
        if tid not in self._customers:
            self._staff.add(tid)

    def lock_as_customer(self, tid: str):
        """
        Mark this id_token as a confirmed customer — prevents any future
        reclassification as staff even if behavioural signals fire.
        """
        self._customers.add(tid)
        self._staff.discard(tid)   # undo any premature flag

    # ── Signal evaluators ─────────────────────────────────────────────────

    def check_uniform(self, crop: np.ndarray, bbox_h: int = 0) -> bool:
        """
        Returns True if the torso crop shows a store-specific uniform colour
        with sufficient coverage.  Also adds UNIFORM_SCORE to the composite
        score and flags the track if threshold crossed.

        NOTE: bbox_h is optional.  If provided and < 60 px, we skip the check
        (crop too small for reliable colour analysis).
        """
        if crop is None or crop.size == 0:
            return False
        if bbox_h > 0 and bbox_h < 60:
            return False

        try:
            h, w = crop.shape[:2]
            if h < 20 or w < 10:
                return False

            # Isolate upper torso region (15 % – 55 % of bbox height)
            torso = crop[int(h * 0.15): int(h * 0.55), :]
            if torso.size == 0:
                return False

            hsv   = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
            total = torso.shape[0] * torso.shape[1]
            if total == 0:
                return False

            spec  = _UNIFORM.get(self.store_id, _UNIFORM["ST1008"])
            mask  = cv2.inRange(hsv, spec["lower"], spec["upper"])
            ratio = cv2.countNonZero(mask) / total

            if ratio >= spec["ratio_min"]:
                return True
        except Exception:
            pass
        return False

    def update_velocity(self, tid: str, x: float, y: float, t: float) -> bool:
        """
        Updates position history and evaluates movement speed.
        A track must sustain high velocity for _VEL_MIN_FRAMES consecutive
        observations before being flagged.  Returns True if currently flagged.
        """
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
                    if self._vel_streak[tid] >= _VEL_MIN_FRAMES:
                        self._score[tid] += _VELOCITY_SCORE_FRAME
                        return self._check_promote(tid)
                else:
                    # Reset streak on slow frame
                    self._vel_streak[tid] = max(0, self._vel_streak[tid] - 1)

        return False

    def record_zone(self, tid: str, zone_id: str) -> bool:
        """
        Records a zone entry.  If the same zone is revisited more than
        revisit_thresh times, it contributes to the composite score.
        Returns True if the track is now flagged as staff.
        """
        if tid in self._customers:
            return False
        if tid in self._staff:
            return True

        self._zone_visits[tid][zone_id] += 1
        count = self._zone_visits[tid][zone_id]

        if count > self.revisit_thresh:
            self._score[tid] += _REVISIT_SCORE_EXTRA
            return self._check_promote(tid)

        return False

    # ── Helpers ───────────────────────────────────────────────────────────

    def _check_promote(self, tid: str) -> bool:
        """Promote tid to staff if composite score reached threshold."""
        if tid in self._customers:
            return False
        if self._score[tid] >= _STAFF_SCORE_THRESHOLD:
            self._staff.add(tid)
            return True
        return False

    def add_uniform_score(self, tid: str) -> bool:
        """
        Called when check_uniform() returns True for a specific tid.
        Adds the uniform signal score and checks for promotion.
        """
        if tid in self._customers:
            return False
        if tid in self._staff:
            return True
        self._score[tid] += _UNIFORM_SCORE
        return self._check_promote(tid)

    # Alias used by detect.py and process_videos.py
    apply_uniform_score = add_uniform_score
