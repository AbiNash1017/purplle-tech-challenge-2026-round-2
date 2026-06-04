"""
Upstash Redis REST API client.
Uses HTTP REST instead of TCP so it works with Upstash free-tier from any network.
Supports: SET (with EX + NX), GET, DEL, PUBLISH (no-op stub, see pub_sub.py).
"""
import httpx
import logging
import time
from app.core.config import settings

logger = logging.getLogger(__name__)

# Shared async httpx client (created lazily)
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            base_url=settings.UPSTASH_REDIS_REST_URL,
            headers={"Authorization": f"Bearer {settings.UPSTASH_REDIS_REST_TOKEN}"},
            timeout=10.0,
        )
    return _http_client


async def _call(*cmd_parts: str):
    """Execute one Redis command via Upstash REST API and return the result field."""
    client = _get_http_client()
    # Upstash REST: POST /pipeline or GET /<cmd>/<arg1>/<arg2>...
    # We use POST pipeline for a single command to handle special chars safely.
    resp = await client.post("/pipeline", json=[list(cmd_parts)])
    resp.raise_for_status()
    results = resp.json()
    # pipeline returns list of {result: ..., error: ...}
    item = results[0]
    if item.get("error"):
        logger.error(f"Upstash Redis error for {cmd_parts[0]}: {item['error']}")
        return None
    return item.get("result")


class UpstashRedis:
    """Minimal async Redis interface backed by Upstash REST with local in-memory fallback."""

    def __init__(self):
        # Local mock database for fallback: key -> (value, expiry_timestamp)
        self._fallback_db = {}
        self._warned_config = False

    def _local_set(self, key: str, value: str, ex: int | None = None, nx: bool = False):
        now = time.time()
        # Prune expired keys
        self._fallback_db = {k: v for k, v in self._fallback_db.items() if v[1] > now}
        
        if nx and key in self._fallback_db:
            return None # NX returns None if key already existed
            
        expiry = now + float(ex) if ex is not None else now + 99999999.0
        self._fallback_db[key] = (str(value), expiry)
        return "OK"

    def _local_get(self, key: str) -> str | None:
        now = time.time()
        val_tuple = self._fallback_db.get(key)
        if val_tuple:
            val, expiry = val_tuple
            if expiry > now:
                return val
            else:
                del self._fallback_db[key]
        return None

    def _local_delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._fallback_db:
                del self._fallback_db[k]
                count += 1
        return count

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False):
        """SET key value [EX seconds] [NX]. Returns 'OK' or None (NX miss)."""
        if not settings.UPSTASH_REDIS_REST_URL or not settings.UPSTASH_REDIS_REST_TOKEN:
            if not self._warned_config:
                logger.warning("Upstash Redis REST credentials are not configured. Using local in-memory fallback.")
                self._warned_config = True
            return self._local_set(key, value, ex, nx)

        try:
            cmd = ["SET", key, str(value)]
            if ex is not None:
                cmd += ["EX", str(ex)]
            if nx:
                cmd.append("NX")
            return await _call(*cmd)
        except Exception as e:
            logger.error(f"Upstash Redis SET connection exception, falling back to local memory: {e}")
            return self._local_set(key, value, ex, nx)

    async def get(self, key: str) -> str | None:
        """GET key. Returns string value or None."""
        if not settings.UPSTASH_REDIS_REST_URL or not settings.UPSTASH_REDIS_REST_TOKEN:
            return self._local_get(key)

        try:
            return await _call("GET", key)
        except Exception as e:
            logger.error(f"Upstash Redis GET connection exception, falling back to local memory: {e}")
            return self._local_get(key)

    async def delete(self, *keys: str):
        """DEL key [key ...]"""
        if not settings.UPSTASH_REDIS_REST_URL or not settings.UPSTASH_REDIS_REST_TOKEN:
            return self._local_delete(*keys)

        try:
            return await _call("DEL", *keys)
        except Exception as e:
            logger.error(f"Upstash Redis DEL connection exception, falling back to local memory: {e}")
            return self._local_delete(*keys)

    async def close(self):
        global _http_client
        if _http_client and not _http_client.is_closed:
            await _http_client.aclose()
        _http_client = None


# Singleton
upstash_redis = UpstashRedis()
