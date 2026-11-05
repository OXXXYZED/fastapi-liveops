import time

from redis.asyncio import Redis


async def hit(redis: Redis, bucket: str, limit: int, window: int) -> tuple[bool, int, int]:
    """Fixed-window counter. Returns (allowed, remaining, retry_after_seconds)."""
    window_id = int(time.time()) // window
    key = f"rl:{bucket}:{window_id}"
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, window, nx=True)
        count, _ = await pipe.execute()
    remaining = max(0, limit - int(count))
    retry_after = window - int(time.time()) % window
    return int(count) <= limit, remaining, retry_after
