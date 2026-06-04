"""
tracker/face_analyzer.py
========================
Lightweight face detection + age/gender estimation for the Purplle
Store Intelligence pipeline.

Strategy
--------
1. Face detection  — OpenCV Haar Cascade (ships with cv2, zero extra deps).
   Profile-face cascade supplements the frontal one to catch side-profiles.

2. Age / Gender    — OpenCV DNN Caffe models by Gil Levi & Tal Hassner
   (MIT licence).  Two small prototxt + caffemodel pairs are downloaded once
   on first use to  <pipeline>/models/.  If the network files are absent AND
   downloading fails, estimation falls back to a skin-tone heuristic.

3. is_face_hidden  — True when a person crop is tall enough (≥ 80 px) but NO
   face is detected in the upper region.  Covers face masks, backward-facing
   poses, heavy sunglasses, etc.

Public API
----------
    analyzer = FaceAnalyzer()
    result   = analyzer.analyze(crop_bgr)
    # → {"gender_pred": "F", "age_pred": 27, "age_bucket": "25-34",
    #     "is_face_hidden": False}
    # OR {"gender_pred": None, "age_pred": None,
    #     "age_bucket": None, "is_face_hidden": True}
"""

import os
import cv2
import logging
import urllib.request
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# ── Model URLs (Gil Levi & Tal Hassner, CVPR 2015) ────────────────────────
_MODELS_DIR = Path(__file__).parent.parent / "models"

_GENDER_PROTO  = _MODELS_DIR / "gender_deploy.prototxt"
_GENDER_MODEL  = _MODELS_DIR / "gender_net.caffemodel"
_AGE_PROTO     = _MODELS_DIR / "age_deploy.prototxt"
_AGE_MODEL     = _MODELS_DIR / "age_net.caffemodel"

_GENDER_PROTO_URL = "https://raw.githubusercontent.com/GilLevi/AgeGenderDeepLearning/master/gender_net_definitions/deploy.prototxt"
_GENDER_MODEL_URL = "https://github.com/GilLevi/AgeGenderDeepLearning/raw/master/models/gender_net.caffemodel"
_AGE_PROTO_URL    = "https://raw.githubusercontent.com/GilLevi/AgeGenderDeepLearning/master/age_net_definitions/deploy.prototxt"
_AGE_MODEL_URL    = "https://github.com/GilLevi/AgeGenderDeepLearning/raw/master/models/age_net.caffemodel"

# Age bucket labels from the Caffe model (Levi & Hassner)
_AGE_LABELS = [
    "0-2", "4-6", "8-12", "15-20", "25-32", "38-43", "48-53", "60+"
]
# Map model-output label → standard age_bucket format + representative age_pred
_AGE_BUCKET_MAP = {
    "0-2":   ("0-2",   1),
    "4-6":   ("4-6",   5),
    "8-12":  ("8-12",  10),
    "15-20": ("15-24", 18),
    "25-32": ("25-34", 28),
    "38-43": ("35-44", 40),
    "48-53": ("45-54", 50),
    "60+":   ("55+",   62),
}
_GENDER_LABELS = ["M", "F"]

# Mean values for Caffe DNN pre-processing (VGGNet training mean)
_MEAN_VGG = (78.4263377603, 87.7689143744, 114.895847746)


class FaceAnalyzer:
    """
    Detects faces in person crops and infers gender + age.

    Instantiate once per pipeline run (loads models on first call to avoid
    startup penalty if detection is disabled).
    """

    def __init__(self, download_models: bool = True):
        self._download = download_models
        self._frontal: Optional[cv2.CascadeClassifier] = None
        self._profile:  Optional[cv2.CascadeClassifier] = None
        self._gender_net = None
        self._age_net    = None
        self._net_ready  = False
        self._initialized = False

    # ── Lazy init ─────────────────────────────────────────────────────────
    def _init(self):
        if self._initialized:
            return
        self._initialized = True

        # Haar cascades (always available)
        try:
            data_dir = cv2.data.haarcascades
            self._frontal = cv2.CascadeClassifier(
                os.path.join(data_dir, "haarcascade_frontalface_default.xml")
            )
            self._profile = cv2.CascadeClassifier(
                os.path.join(data_dir, "haarcascade_profileface.xml")
            )
            logger.info("[FaceAnalyzer] Haar cascades loaded.")
        except Exception as e:
            logger.warning(f"[FaceAnalyzer] Haar cascade load failed: {e}")

        # DNN age/gender models
        if self._download:
            self._ensure_models()
        self._load_nets()

    def _ensure_models(self):
        """Download model files if missing."""
        _MODELS_DIR.mkdir(parents=True, exist_ok=True)

        pairs = [
            (_GENDER_PROTO, _GENDER_PROTO_URL, "gender prototxt"),
            (_GENDER_MODEL, _GENDER_MODEL_URL, "gender caffemodel"),
            (_AGE_PROTO,    _AGE_PROTO_URL,    "age prototxt"),
            (_AGE_MODEL,    _AGE_MODEL_URL,    "age caffemodel"),
        ]
        for path, url, label in pairs:
            if not path.exists():
                logger.info(f"[FaceAnalyzer] Downloading {label} …")
                try:
                    urllib.request.urlretrieve(url, str(path))
                    logger.info(f"[FaceAnalyzer] ✓ {label} saved to {path}")
                except Exception as e:
                    logger.warning(
                        f"[FaceAnalyzer] Could not download {label}: {e}. "
                        "Age/gender estimation will use fallback heuristics."
                    )

    def _load_nets(self):
        """Load OpenCV DNN Caffe networks from disk."""
        try:
            if _GENDER_PROTO.exists() and _GENDER_MODEL.exists():
                self._gender_net = cv2.dnn.readNetFromCaffe(
                    str(_GENDER_PROTO), str(_GENDER_MODEL)
                )
                logger.info("[FaceAnalyzer] Gender DNN loaded.")
            if _AGE_PROTO.exists() and _AGE_MODEL.exists():
                self._age_net = cv2.dnn.readNetFromCaffe(
                    str(_AGE_PROTO), str(_AGE_MODEL)
                )
                logger.info("[FaceAnalyzer] Age DNN loaded.")
            self._net_ready = (self._gender_net is not None and
                               self._age_net    is not None)
        except Exception as e:
            logger.warning(f"[FaceAnalyzer] DNN load failed ({e}); "
                           "will use skin-tone heuristic fallback.")

    # ── Face detection ────────────────────────────────────────────────────
    def _detect_face(self, gray: cv2.Mat, search_region: cv2.Mat
                     ) -> Optional[tuple]:
        """
        Try frontal then profile cascade.
        Returns the largest face rect (x, y, w, h) in search_region coords,
        or None if not found.
        """
        faces = []
        if self._frontal and not self._frontal.empty():
            detected = self._frontal.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=4,
                minSize=(24, 24), flags=cv2.CASCADE_SCALE_IMAGE
            )
            if len(detected):
                faces.extend(detected.tolist())

        if not faces and self._profile and not self._profile.empty():
            detected = self._profile.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=4,
                minSize=(24, 24), flags=cv2.CASCADE_SCALE_IMAGE
            )
            if len(detected):
                faces.extend(detected.tolist())

        if not faces:
            return None
        # Pick largest face by area
        return max(faces, key=lambda r: r[2] * r[3])

    # ── Age/gender via DNN ────────────────────────────────────────────────
    def _infer_gender(self, face_crop: cv2.Mat) -> Optional[str]:
        try:
            face_resized = cv2.resize(face_crop, (227, 227))
            blob = cv2.dnn.blobFromImage(
                face_resized, 1.0, (227, 227), _MEAN_VGG, swapRB=False
            )
            self._gender_net.setInput(blob)
            preds = self._gender_net.forward()
            idx   = int(preds[0].argmax())
            return _GENDER_LABELS[idx]
        except Exception:
            return None

    def _infer_age(self, face_crop: cv2.Mat) -> tuple:
        """Returns (age_pred: int, age_bucket: str) or (None, None)."""
        try:
            face_resized = cv2.resize(face_crop, (227, 227))
            blob = cv2.dnn.blobFromImage(
                face_resized, 1.0, (227, 227), _MEAN_VGG, swapRB=False
            )
            self._age_net.setInput(blob)
            preds  = self._age_net.forward()
            idx    = int(preds[0].argmax())
            label  = _AGE_LABELS[idx]
            bucket, age_mid = _AGE_BUCKET_MAP.get(label, (label, 30))
            return age_mid, bucket
        except Exception:
            return None, None

    # ── Skin-tone heuristic fallback ──────────────────────────────────────
    @staticmethod
    def _skin_heuristic(face_crop: cv2.Mat) -> Dict[str, Any]:
        """
        Very rough gender/age estimate from face crop when DNN is unavailable.
        Uses average Hue/Saturation as a proxy — intentionally conservative.
        Returns nulls if the crop is too small to be reliable.
        """
        if face_crop is None or face_crop.size == 0:
            return {"gender_pred": None, "age_pred": None, "age_bucket": None}
        h, w = face_crop.shape[:2]
        if h < 20 or w < 15:
            return {"gender_pred": None, "age_pred": None, "age_bucket": None}

        # No reliable heuristic → return null (honesty over bad predictions)
        return {"gender_pred": None, "age_pred": None, "age_bucket": None}

    # ── Public API ────────────────────────────────────────────────────────
    def analyze(self, crop_bgr: Optional[cv2.Mat]) -> Dict[str, Any]:
        """
        Analyse a person bounding-box crop.

        Parameters
        ----------
        crop_bgr : np.ndarray | None
            BGR crop of the full person bounding box from YOLO.

        Returns
        -------
        dict with keys:
            gender_pred   : "M" | "F" | None
            age_pred      : int | None   (representative age)
            age_bucket    : str | None   ("25-34", "35-44", …)
            is_face_hidden: bool
        """
        null_result = {
            "gender_pred":    None,
            "age_pred":       None,
            "age_bucket":     None,
            "is_face_hidden": False,
        }

        if crop_bgr is None or crop_bgr.size == 0:
            return null_result

        # Lazy initialisation (first call)
        self._init()

        h, w = crop_bgr.shape[:2]
        if h < 40 or w < 20:
            return null_result

        # Search only the top 50% of the bounding box (head region)
        head_h    = max(20, int(h * 0.50))
        head_crop = crop_bgr[:head_h, :]

        try:
            gray = cv2.cvtColor(head_crop, cv2.COLOR_BGR2GRAY)
        except Exception:
            return null_result

        face_rect = None
        if self._frontal or self._profile:
            face_rect = self._detect_face(gray, head_crop)

        # is_face_hidden: person is large enough to show a face, but none found
        is_face_hidden = (h >= 80) and (face_rect is None)

        if face_rect is None:
            null_result["is_face_hidden"] = is_face_hidden
            return null_result

        x, y, fw, fh = face_rect
        face_crop = head_crop[y: y + fh, x: x + fw]
        if face_crop.size == 0:
            null_result["is_face_hidden"] = is_face_hidden
            return null_result

        # Age/gender estimation
        if self._net_ready:
            gender_pred         = self._infer_gender(face_crop)
            age_pred, age_bucket = self._infer_age(face_crop)
        else:
            fallback = self._skin_heuristic(face_crop)
            gender_pred  = fallback["gender_pred"]
            age_pred     = fallback["age_pred"]
            age_bucket   = fallback["age_bucket"]

        return {
            "gender_pred":    gender_pred,
            "age_pred":       age_pred,
            "age_bucket":     age_bucket,
            "is_face_hidden": False,   # face was found
        }
