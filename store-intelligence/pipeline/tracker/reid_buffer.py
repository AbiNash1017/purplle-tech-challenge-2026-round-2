import logging
import base64
import json
import numpy as np
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)

# Feature Extractor Setup with PyTorch Fallback
HAS_TORCH = False
model = None
device = "cpu"

try:
    import torch
    import torchvision.transforms as T
    from torchvision.models import mobilenet_v3_small, MobileNet_V3_Small_Weights
    
    # Check GPU availability
    if torch.cuda.is_available():
        device = "cuda"
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        device = "mps"
        
    weights = MobileNet_V3_Small_Weights.DEFAULT
    model = mobilenet_v3_small(weights=weights).eval().to(device)
    # Remove classifier head to get feature embeddings (output dim: 576)
    model.classifier = torch.nn.Identity()
    
    preprocess = T.Compose([
        T.ToPILImage(),
        T.Resize((128, 64)),
        T.ToTensor(),
        T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
    ])
    HAS_TORCH = True
    logger.info(f"PyTorch MobileNetV3 loaded successfully. Running Re-ID on {device}.")
except Exception as ex:
    logger.warning(f"Could not load PyTorch/torchvision ({ex}). Falling back to Color Histogram Re-ID.")

class ReIDTracker:
    def __init__(self, redis_client=None, threshold: float = 0.82, ttl_seconds: int = 300):
        self.redis = redis_client
        self.threshold = threshold
        self.ttl = ttl_seconds
        
        # Local memory fallback if redis is not provided
        self.local_buffer = {}  # key -> (track_id, embedding_array, timestamp)

    def extract_embedding(self, crop_image: np.ndarray) -> np.ndarray:
        """
        Extracts a feature vector from a person crop image.
        If PyTorch is loaded, uses MobileNetV3. Otherwise, falls back to a 3D HSV Color Histogram.
        """
        if crop_image is None or crop_image.size == 0:
            return np.zeros((128,), dtype=np.float32)
            
        if HAS_TORCH and model is not None:
            try:
                # crop_image is BGR from OpenCV, convert to RGB
                rgb_crop = crop_image[:, :, ::-1]
                tensor = preprocess(rgb_crop).unsqueeze(0).to(device)
                with torch.no_grad():
                    embedding = model(tensor).squeeze().cpu().numpy()
                # Normalize vector to unit length
                norm = np.linalg.norm(embedding)
                if norm > 0:
                    embedding = embedding / norm
                return embedding.astype(np.float32)
            except Exception as e:
                logger.error(f"Failed extracting deep embedding: {e}. Falling back to histogram.")
                
        # Color Histogram Fallback (HSV 3D hist)
        try:
            import cv2
            hsv = cv2.cvtColor(crop_image, cv2.COLOR_BGR2HSV)
            # 8 hue bins, 8 saturation bins, 8 value bins
            hist = cv2.calcHist([hsv], [0, 1, 2], None, [8, 8, 8], [0, 180, 0, 256, 0, 256])
            cv2.normalize(hist, hist)
            return hist.flatten().astype(np.float32)
        except Exception as e:
            # Absolute fallback
            logger.error(f"Histogram failed: {e}. Returning zero vector.")
            return np.zeros((64,), dtype=np.float32)

    async def lookup_and_register(self, store_id: str, crop_image: np.ndarray, current_track_id: str) -> str:
        """
        Looks up a crop in the recent tracks buffer.
        If a match is found (cosine similarity > threshold), returns the original track_id.
        Otherwise, registers the current track_id and returns it.
        """
        embedding = self.extract_embedding(crop_image)
        
        if np.linalg.norm(embedding) == 0:
            return current_track_id
            
        # 1. Fetch recent tracks for this store
        recent_tracks = await self._get_recent_embeddings(store_id)
        
        best_match_id = None
        best_similarity = -1.0
        
        for ref_id, ref_emb in recent_tracks:
            # Cosine similarity (since vectors are normalized, it is just dot product)
            similarity = np.dot(embedding, ref_emb)
            if similarity > best_similarity:
                best_similarity = similarity
                best_match_id = ref_id
                
        if best_match_id and best_similarity >= self.threshold:
            logger.info(f"Re-ID matched track {current_track_id} to old track {best_match_id} (sim: {best_similarity:.3f})")
            # Re-register under the matched track_id to refresh its TTL
            await self._register_embedding(store_id, best_match_id, embedding)
            return best_match_id
            
        # No match found, register this new track
        await self._register_embedding(store_id, current_track_id, embedding)
        return current_track_id

    async def _get_recent_embeddings(self, store_id: str) -> List[Tuple[str, np.ndarray]]:
        if self.redis is not None:
            try:
                # Upstash Redis: fetch keys matching pattern
                pattern = f"reid:{store_id}:*"
                keys = await self.redis.keys(pattern)
                results = []
                for key in keys:
                    track_id = key.split(":")[-1]
                    emb_b64 = await self.redis.get(key)
                    if emb_b64:
                        emb_bytes = base64.b64decode(emb_b64.encode("utf-8"))
                        emb_arr = np.frombuffer(emb_bytes, dtype=np.float32)
                        results.append((track_id, emb_arr))
                return results
            except Exception as e:
                logger.error(f"Redis Re-ID fetch failed: {e}. Using local fallback.")
                
        # Local buffer fetch & cleanup expired items
        import time
        now = time.time()
        expired_keys = [k for k, v in self.local_buffer.items() if now - v[2] > self.ttl]
        for k in expired_keys:
            del self.local_buffer[k]
            
        return [
            (v[0], v[1]) 
            for k, v in self.local_buffer.items() 
            if k.startswith(f"{store_id}:")
        ]

    async def _register_embedding(self, store_id: str, track_id: str, embedding: np.ndarray):
        if self.redis is not None:
            try:
                key = f"reid:{store_id}:{track_id}"
                emb_b64 = base64.b64encode(embedding.tobytes()).decode("utf-8")
                await self.redis.set(key, emb_b64, ex=self.ttl)
                return
            except Exception as e:
                logger.error(f"Redis Re-ID write failed: {e}. Using local fallback.")
                
        # Local fallback register
        import time
        key = f"{store_id}:{track_id}"
        self.local_buffer[key] = (track_id, embedding, time.time())
