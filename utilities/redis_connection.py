import os
import redis

_client = None


def get_redis():
    global _client
    if _client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _client = redis.from_url(url, decode_responses=True, socket_timeout=5)
    return _client
