"""
Two small caches, both safe because what they cache never changes.

- ResultCache: computed JSON results in memory, keyed by
  (dataset_id, version, kind, params). A new version is a new key, so a cached
  result can never be stale. Size-capped in bytes, least-recently-used out first.

- FileCache: downloaded Parquet files on local disk, keyed by storage path.
  Storage paths are unique per version and never overwritten, so a cached file
  is always correct. Size-capped; oldest-used files are deleted first.
  On Render the disk is wiped on restart, which is fine: it's only a cache.
"""

import hashlib
import json
import logging
import os
import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger("app.cache")


def params_key(params: dict | None) -> str:
    if not params:
        return ""
    raw = json.dumps(params, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class ResultCache:
    def __init__(self, max_bytes: int) -> None:
        self._max = max_bytes
        self._items: OrderedDict[tuple, tuple[Any, int]] = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()

    def get(self, key: tuple) -> Any | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            self._items.move_to_end(key)
            return item[0]

    def put(self, key: tuple, value: Any) -> None:
        if self._max <= 0:
            return
        size = len(json.dumps(value, default=str))
        if size > self._max // 4:  # never let one result evict everything
            return
        with self._lock:
            if key in self._items:
                self._size -= self._items.pop(key)[1]
            self._items[key] = (value, size)
            self._size += size
            while self._size > self._max and self._items:
                _, (_, evicted) = self._items.popitem(last=False)
                self._size -= evicted

    def __len__(self) -> int:
        return len(self._items)


class FileCache:
    def __init__(self, directory: Path, max_bytes: int) -> None:
        self._dir = directory
        self._dir.mkdir(parents=True, exist_ok=True)
        self._max = max_bytes
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def _local_path(self, storage_path: str) -> Path:
        digest = hashlib.sha256(storage_path.encode()).hexdigest()[:40]
        return self._dir / f"{digest}.parquet"

    def get_or_fetch(self, storage_path: str, fetch: Callable[[Path], None]) -> Path:
        """Return a local copy, downloading it once (concurrent callers wait, not re-download)."""
        target = self._local_path(storage_path)
        if target.exists():
            os.utime(target)  # mark as recently used
            return target
        with self._lock_for(storage_path):
            if target.exists():
                return target
            partial = target.with_suffix(f".part-{threading.get_ident()}")
            try:
                fetch(partial)
                os.replace(partial, target)  # atomic: readers never see half a file
            finally:
                partial.unlink(missing_ok=True)
        self._evict()
        return target

    def _evict(self) -> None:
        files = []
        for path in self._dir.glob("*.parquet"):
            try:
                stat = path.stat()  # mtime is refreshed on every hit (os.utime above)
                files.append((stat.st_mtime_ns, stat.st_size, path))
            except FileNotFoundError:
                continue
        total = sum(size for _, size, _ in files)
        if total <= self._max:
            return
        for _, size, path in sorted(files):
            if total <= self._max:
                break
            try:
                path.unlink()
                total -= size
            except FileNotFoundError:
                pass
        logger.info("file cache trimmed to %.1f MB", total / 1e6)
