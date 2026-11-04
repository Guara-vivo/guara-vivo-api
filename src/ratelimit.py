"""In-process sliding-window rate limiter.

State lives in this process: with several API instances each one counts on its own, so the
effective limit is multiplied by the instance count. Move to Redis (or another shared store)
before running more than one instance.
"""
from collections import deque
from time import monotonic
from typing import Callable


class RateLimiter:
    def __init__(
        self,
        max_attempts: int,
        window_seconds: float,
        max_keys: int = 10_000,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def _prune(self, hits: deque[float], now: float) -> None:
        while hits and now - hits[0] >= self.window_seconds:
            hits.popleft()

    def _make_room(self, now: float) -> None:
        # Drop keys whose window has fully expired first; they carry no information.
        for key in list(self._hits):
            hits = self._hits[key]
            self._prune(hits, now)
            if not hits:
                del self._hits[key]
        # Still full of live keys: evict the least recently active, never everything.
        while len(self._hits) >= self.max_keys:
            oldest = min(self._hits, key=lambda key: self._hits[key][-1])
            del self._hits[oldest]

    def hit(self, key: str) -> tuple[bool, float]:
        """Count an attempt. Returns (allowed, seconds_until_allowed)."""
        now = self._clock()
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= self.max_keys:
                self._make_room(now)
            hits = self._hits[key] = deque()
        self._prune(hits, now)

        if len(hits) >= self.max_attempts:
            return False, max(0.0, self.window_seconds - (now - hits[0]))

        hits.append(now)
        return True, 0.0
