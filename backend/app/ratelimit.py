"""Per-client rate limiting.

Clients are identified by API key (or by IP when auth is disabled). Two scopes:
"default" for every authenticated endpoint, and a stricter "execute" for endpoints that
run agents and spend provider money. Uses Redis (fixed one-minute windows, shared by all
API processes) when AGENTEVAL_REDIS_URL is set, else an in-process token bucket.
"""

import threading
import time

from fastapi import Depends, HTTPException, Request, status

from app.auth import Principal, require_api_key
from app.config import get_settings


class MemoryLimiter:
    """Token bucket per (scope, client): `limit` tokens, refilled continuously."""

    def __init__(self):
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int) -> float | None:
        """Consume one token. Returns None if allowed, else seconds until retry."""
        now = time.monotonic()
        rate = limit / 60.0
        with self._lock:
            tokens, last = self._buckets.get(key, (float(limit), now))
            tokens = min(float(limit), tokens + (now - last) * rate)
            if tokens >= 1:
                self._buckets[key] = (tokens - 1, now)
                return None
            self._buckets[key] = (tokens, now)
            return (1 - tokens) / rate

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


class RedisLimiter:
    """Fixed one-minute windows in Redis, so limits hold across API replicas."""

    def __init__(self, url: str):
        import redis

        self._redis = redis.Redis.from_url(url)

    def hit(self, key: str, limit: int) -> float | None:
        window = int(time.time() // 60)
        rkey = f"agenteval:rl:{key}:{window}"
        pipe = self._redis.pipeline()
        pipe.incr(rkey)
        pipe.expire(rkey, 120)
        count = pipe.execute()[0]
        return None if count <= limit else 60 - (time.time() % 60)

    def reset(self) -> None:  # pragma: no cover - test helper for the memory limiter
        pass


_limiter: MemoryLimiter | RedisLimiter | None = None


def get_limiter() -> MemoryLimiter | RedisLimiter:
    global _limiter
    if _limiter is None:
        url = get_settings().redis_url
        _limiter = RedisLimiter(url) if url else MemoryLimiter()
    return _limiter


def rate_limit(scope: str):
    def dependency(request: Request, principal: Principal = Depends(require_api_key)) -> None:
        settings = get_settings()
        limit = (
            settings.execute_rate_limit_per_minute
            if scope == "execute"
            else settings.rate_limit_per_minute
        )
        client = (
            principal.id
            if principal.id != "anonymous"
            else (request.client.host if request.client else "unknown")
        )
        retry_after = get_limiter().hit(f"{scope}:{client}", limit)
        if retry_after is not None:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"rate limit exceeded ({limit}/min for {scope})",
                headers={"Retry-After": str(max(1, round(retry_after)))},
            )

    return dependency
