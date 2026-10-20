"""
The ONE way any endpoint reads dataset data.

    resolve()  -> ownership check + which version (current unless asked)
    frame()    -> the version's Parquet, from local disk cache or Storage
    cached()   -> memory cache -> database cache -> compute -> store

v1 had nine services and only one of them could restore a dataset after a
restart. Here there is a single path, so that bug can't come back.
"""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from uuid import UUID

import polars as pl

from app.core.cache import FileCache, ResultCache, params_key
from app.core.errors import NotFoundError
from app.db.models import DatasetRecord, VersionRecord
from app.db.repository import DatasetRepository
from app.storage.client import ObjectStorage

logger = logging.getLogger("app.services.analysis")

# Bump when the analysis logic changes: results cached by an older engine (in memory
# or in Postgres) are then simply not found, and get recomputed with the new rules.
ENGINE_VERSION = "2026-10-20.matches"

# Results worth persisting in Postgres: they're requested often and cost a
# full scan. Parameterised results (charts, relationships) stay in memory.
PERSISTED_KINDS = {"overview", "columns", "health"}


@dataclass(frozen=True)
class Resolved:
    dataset: DatasetRecord
    version: VersionRecord


class AnalysisService:
    def __init__(
        self,
        repo: DatasetRepository,
        storage: ObjectStorage,
        files: FileCache,
        results: ResultCache,
        heavy: Callable[[], AbstractContextManager] = nullcontext,
    ) -> None:
        self.repo = repo
        self.storage = storage
        self.files = files
        self.results = results
        self.heavy = heavy  # JobLimiter.heavy: only cache misses take a heavy slot

    def resolve(self, user_id: UUID, dataset_id: UUID, version: int | None = None) -> Resolved:
        found = self.repo.get_dataset_with_version(user_id, dataset_id, version)  # one round trip
        if found is None:
            raise NotFoundError("Dataset not found.", code="dataset_not_found")
        dataset, record = found
        number = version or dataset.current_version
        if record is None:
            raise NotFoundError(
                f"Version {number} does not exist (it may have been pruned).",
                code="version_not_found",
                details={"current_version": dataset.current_version},
            )
        return Resolved(dataset, record)

    def frame(self, resolved: Resolved, columns: list[str] | None = None) -> pl.DataFrame:
        path = self.files.get_or_fetch(
            resolved.version.parquet_path,
            lambda local: self.storage.download(resolved.version.parquet_path, local),
        )
        return pl.read_parquet(path, columns=columns)

    def lazy(self, resolved: Resolved) -> pl.LazyFrame:
        path = self.files.get_or_fetch(
            resolved.version.parquet_path,
            lambda local: self.storage.download(resolved.version.parquet_path, local),
        )
        return pl.scan_parquet(path)

    def cached(self, resolved: Resolved, kind: str, params: dict | None, compute: Callable[[], dict]) -> dict:
        dataset_id, version = resolved.dataset.id, resolved.version.version
        hashed = params_key({"params": params, "engine": ENGINE_VERSION})
        key = (dataset_id, version, kind, hashed)

        hit = self.results.get(key)
        if hit is not None:
            return hit

        if kind in PERSISTED_KINDS:
            stored = self.repo.get_analysis(dataset_id, version, kind, hashed)
            if stored is not None:
                self.results.put(key, stored)
                return stored

        with self.heavy():
            hit = self.results.get(key)  # another request may have computed it while we waited
            if hit is not None:
                return hit
            result = compute()
        self.results.put(key, result)
        if kind in PERSISTED_KINDS:
            try:
                self.repo.put_analysis(dataset_id, version, kind, hashed, result)
            except Exception:  # a cache write must never fail the request
                logger.exception("could not persist %s for %s v%s", kind, dataset_id, version)
        return result
