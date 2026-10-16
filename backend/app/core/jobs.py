"""
Heavy-work limiter.

All blocking work runs in a worker thread (`run`), so the event loop stays free
to answer other requests: the v1 freeze bug.

Only HEAVY work (parsing an upload, computing a profile on a cache miss,
applying a cleaning step, writing an export) takes one of the limited slots
(`heavy()`); at most `max_concurrent_jobs` run at once, because two big files
processed simultaneously could exhaust 512 MB. Cheap work (cache hits, row
pages, version lists) never waits behind a heavy job.

If no slot frees up within the queue timeout, the request gets 503
`server_busy` instead of piling up.
"""

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TypeVar

from starlette.concurrency import run_in_threadpool

from app.core.errors import ServiceUnavailableError

T = TypeVar("T")


class JobLimiter:
    def __init__(self, max_concurrent: int, queue_timeout: float) -> None:
        self._slots = threading.BoundedSemaphore(max_concurrent)
        self._timeout = queue_timeout

    @contextmanager
    def heavy(self) -> Iterator[None]:
        if not self._slots.acquire(timeout=self._timeout):
            raise ServiceUnavailableError(
                "The server is busy processing other files. Please retry in a moment.", code="server_busy"
            )
        try:
            yield
        finally:
            self._slots.release()

    async def run(self, func: Callable[..., T], *args, heavy: bool = False) -> T:
        """Run `func` in a worker thread; with heavy=True it also holds a heavy slot throughout."""
        if not heavy:
            return await run_in_threadpool(func, *args)

        def guarded() -> T:
            with self.heavy():
                return func(*args)

        return await run_in_threadpool(guarded)
