"""Fixed-window rate limiting in Redis (manual reviews: 5 per PR per hour)."""

from typing import Protocol

from redis.asyncio import Redis


class RateLimiter(Protocol):
    async def hit(self, key: str, *, limit: int, window_seconds: int) -> bool:
        """Count one use; True if still within the limit."""
        ...


class RedisRateLimiter:
    """INCR a key and give it a TTL on first use; the window resets when it expires."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> bool:
        count = int(await self._redis.incr(key))
        if count == 1:
            await self._redis.expire(key, window_seconds)
        return count <= limit


def manual_review_key(pull_request_id: int) -> str:
    # Shared by /reviewpilot review and the dashboard's re-review button.
    return f"cmd:review:{pull_request_id}"
