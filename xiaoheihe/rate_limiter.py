from __future__ import annotations

import asyncio
import random
from collections.abc import Callable
from time import monotonic


class AsyncRateLimiter:
    def __init__(
        self,
        min_interval_seconds: float,
        *,
        jitter_seconds: float = 0.15,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._min_interval = max(0.0, float(min_interval_seconds))
        self._jitter = max(0.0, float(jitter_seconds))
        self._clock = clock
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = self._clock()
            delay = max(0.0, self._next_allowed - now)
            if delay:
                await asyncio.sleep(delay)
            next_jitter = random.uniform(0.0, self._jitter)
            self._next_allowed = self._clock() + self._min_interval + next_jitter
