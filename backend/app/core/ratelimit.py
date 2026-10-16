"""
Tiny in-memory sliding-window rate limiter (per user, per action).

Good enough for one server process. If we ever run several instances, this
moves to Postgres or Redis; the interface stays the same.
"""

import threading
import time
from collections import defaultdict, deque

from app.core.errors import TooManyRequestsError


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float) -> None:
        self._limit = limit
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                retry_after = int(self._window - (now - hits[0])) + 1
                raise TooManyRequestsError(
                    "Too many uploads. Please wait before uploading again.",
                    code="rate_limited",
                    details={"retry_after_seconds": retry_after},
                )
            hits.append(now)
