import asyncio
import math
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple


class PerModelRateLimiter:
    def __init__(self, limits_per_minute: Dict[str, int]) -> None:
        self._limits = limits_per_minute
        self._requests: Dict[str, Deque[float]] = defaultdict(deque)
        self._locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def acquire(self, model: str) -> Tuple[bool, int]:
        limit = self._limits[model]
        now = time.monotonic()
        window_start = now - 60.0
        async with self._locks[model]:
            timestamps = self._requests[model]
            while timestamps and timestamps[0] <= window_start:
                timestamps.popleft()
            if len(timestamps) >= limit:
                retry_after = max(1, math.ceil(60.0 - (now - timestamps[0])))
                return False, retry_after
            timestamps.append(now)
            return True, 0
