import os
import time
import redis

_client = None


class _InMemoryRedis:
    """Dev-only in-memory Redis substitute used when no Redis server is reachable."""
    def __init__(self):
        self._store = {}   # key -> value
        self._expiry = {}  # key -> expire_at (epoch seconds)

    def _alive(self, key):
        exp = self._expiry.get(key)
        if exp and time.time() > exp:
            self._store.pop(key, None)
            self._expiry.pop(key, None)
            return False
        return key in self._store

    def get(self, key):
        return self._store[key] if self._alive(key) else None

    def setex(self, key, ttl, value):
        self._store[key] = value
        self._expiry[key] = time.time() + ttl

    def incr(self, key):
        if not self._alive(key):
            self._store[key] = 0
        self._store[key] += 1
        return self._store[key]

    def expire(self, key, ttl):
        if key in self._store:
            self._expiry[key] = time.time() + ttl

    def delete(self, key):
        self._store.pop(key, None)
        self._expiry.pop(key, None)


def get_redis():
    global _client
    if _client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        try:
            client = redis.from_url(url, decode_responses=True, socket_timeout=2)
            client.ping()
            _client = client
        except Exception:
            print("[Redis] Not reachable — using in-memory store (dev only)")
            _client = _InMemoryRedis()
    return _client
