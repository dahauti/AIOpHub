"""Client-side request pacing: rolling per-minute cap plus a minimum gap between calls."""

import asyncio
import time
from collections import deque
from collections.abc import Callable

from src.adapters.resilience import Sleep

MAX_REQUESTS_PER_MINUTE = 150
MIN_INTERVAL_SECONDS = 0.5
_WINDOW_SECONDS = 60.0


class RatePacer:
    """Rolling 60s window cap plus a minimum gap between consecutive calls."""

    def __init__(
        self,
        max_per_minute: int = MAX_REQUESTS_PER_MINUTE,
        min_interval: float = MIN_INTERVAL_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._max = max_per_minute
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._sent: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until one more request is allowed, then record it."""
        async with self._lock:
            wait = self._wait_needed(self._clock())
            if wait > 0:
                await self._sleep(wait)
            now = self._clock()
            self._prune(now)
            self._sent.append(now)

    def _wait_needed(self, now: float) -> float:
        self._prune(now)
        wait = 0.0
        if self._sent:
            wait = self._sent[-1] + self._min_interval - now
        if len(self._sent) >= self._max:
            wait = max(wait, self._sent[0] + _WINDOW_SECONDS - now)
        return max(wait, 0.0)

    def _prune(self, now: float) -> None:
        while self._sent and now - self._sent[0] >= _WINDOW_SECONDS:
            self._sent.popleft()
