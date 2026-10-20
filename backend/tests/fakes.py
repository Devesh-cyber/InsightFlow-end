"""
In-memory stand-ins for Postgres and Supabase Storage.

They implement the same methods as the real classes, with the same rules
(ownership, quota, optimistic concurrency, pruning), so service and API tests
exercise real behaviour without any network. The real Postgres SQL is tested
separately in test_repository_postgres.py.
"""

import shutil
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.core.errors import NotFoundError
from app.db.models import DatasetRecord, NewDataset, NewVersion, Usage, VersionRecord
from app.db.repository import QuotaExceededError, VersionConflictError
from app.storage.client import StorageError


class FakeStorage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.fail_uploads = False
        self.uploads: list[str] = []

    def _path(self, key: str) -> Path:
        return self.root / key

    def upload(self, path: str, local_file: Path, content_type: str) -> None:
        if self.fail_uploads:
            raise StorageError()
        target = self._path(path)
        if target.exists():
            raise AssertionError(f"storage path reused: {path}")  # we must never overwrite
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_file, target)
        self.uploads.append(path)

    def download(self, path: str, local_file: Path) -> None:
        shutil.copyfile(self._path(path), local_file)

    def delete(self, paths: list[str]) -> None:
        for path in paths:
            self._path(path).unlink(missing_ok=True)

    def keys(self) -> set[str]:
        return {str(p.relative_to(self.root)) for p in self.root.rglob("*") if p.is_file()}


class FakeRepository:
    def __init__(self) -> None:
        self.datasets: dict[UUID, DatasetRecord] = {}
        self.versions: dict[tuple[UUID, int], VersionRecord] = {}
        self.fail_create = False

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def count_datasets(self, user_id: UUID) -> int:
        return sum(1 for d in self.datasets.values() if d.user_id == user_id)

    def usage(self, user_id: UUID) -> Usage:
        mine = [d for d in self.datasets.values() if d.user_id == user_id]
        paths = {v.parquet_path: v.size_bytes for (did, _), v in self.versions.items() if did in {d.id for d in mine}}
        return Usage(datasets=len(mine), storage_bytes=sum(d.source_size_bytes for d in mine) + sum(paths.values()))

    def create_dataset(self, dataset: NewDataset, version: NewVersion, max_datasets: int) -> DatasetRecord:
        if self.fail_create:
            raise RuntimeError("database is down")
        if self.count_datasets(dataset.user_id) >= max_datasets:
            raise QuotaExceededError("quota", details={"limit": max_datasets})
        now = self._now()
        record = DatasetRecord(
            id=dataset.id, user_id=dataset.user_id, name=dataset.name, source_filename=dataset.source_filename,
            source_format=dataset.source_format, source_path=dataset.source_path,
            source_size_bytes=dataset.source_size_bytes, sheet_name=dataset.sheet_name,
            sheet_names=dataset.sheet_names, current_version=1, status="ready",
            row_count=version.row_count, column_count=version.column_count, created_at=now, updated_at=now,
        )
        self.datasets[record.id] = record
        self._add(record.id, 1, version)
        return record

    def _add(self, dataset_id: UUID, number: int, version: NewVersion) -> None:
        self.versions[(dataset_id, number)] = VersionRecord(
            dataset_id=dataset_id, version=number, parquet_path=version.parquet_path,
            row_count=version.row_count, column_count=version.column_count, size_bytes=version.size_bytes,
            operation=version.operation, summary=version.summary, created_at=self._now(),
        )

    def list_datasets(self, user_id: UUID) -> list[DatasetRecord]:
        return sorted((d for d in self.datasets.values() if d.user_id == user_id), key=lambda d: d.updated_at, reverse=True)

    def get_dataset(self, user_id: UUID, dataset_id: UUID) -> DatasetRecord | None:
        record = self.datasets.get(dataset_id)
        return record if record and record.user_id == user_id else None

    def get_version(self, user_id: UUID, dataset_id: UUID, version: int) -> VersionRecord | None:
        return self.versions.get((dataset_id, version)) if self.get_dataset(user_id, dataset_id) else None

    def get_dataset_with_version(self, user_id: UUID, dataset_id: UUID, version: int | None = None):
        record = self.get_dataset(user_id, dataset_id)
        if record is None:
            return None
        return record, self.versions.get((dataset_id, version or record.current_version))

    def rename_dataset(self, user_id: UUID, dataset_id: UUID, name: str) -> DatasetRecord | None:
        record = self.get_dataset(user_id, dataset_id)
        if record is None:
            return None
        record = replace(record, name=name, updated_at=self._now())
        self.datasets[dataset_id] = record
        return record

    def delete_dataset(self, user_id: UUID, dataset_id: UUID) -> list[str] | None:
        record = self.get_dataset(user_id, dataset_id)
        if record is None:
            return None
        paths = [record.source_path] + sorted({v.parquet_path for (d, _), v in self.versions.items() if d == dataset_id})
        del self.datasets[dataset_id]
        for key in [k for k in self.versions if k[0] == dataset_id]:
            del self.versions[key]
        return paths

    def get_checks(self, user_id: UUID, dataset_id: UUID) -> list[dict] | None:
        if self.get_dataset(user_id, dataset_id) is None:
            return None
        return list(getattr(self, "checks", {}).get(dataset_id, []))

    def set_checks(self, user_id: UUID, dataset_id: UUID, checks: list[dict]) -> list[dict] | None:
        if self.get_dataset(user_id, dataset_id) is None:
            return None
        self.checks = getattr(self, "checks", {})
        self.checks[dataset_id] = list(checks)
        return list(checks)

    def list_versions(self, user_id: UUID, dataset_id: UUID) -> list[VersionRecord]:
        if self.get_dataset(user_id, dataset_id) is None:
            return []
        return sorted((v for (d, _), v in self.versions.items() if d == dataset_id), key=lambda v: v.version, reverse=True)

    def get_analysis(self, dataset_id, version, kind, params_hash):
        self.analysis_reads = getattr(self, "analysis_reads", 0) + 1
        return getattr(self, "analysis", {}).get((dataset_id, version, kind, params_hash))

    def put_analysis(self, dataset_id, version, kind, params_hash, result):
        if not hasattr(self, "analysis"):
            self.analysis = {}
        import json

        self.analysis[(dataset_id, version, kind, params_hash)] = json.loads(json.dumps(result))  # like JSONB

    def add_version(self, user_id, dataset_id, expected_version, version: NewVersion, keep_versions):
        record = self.get_dataset(user_id, dataset_id)
        if record is None:
            raise NotFoundError("Dataset not found.", code="dataset_not_found")
        if record.current_version != expected_version:
            raise VersionConflictError(record.current_version)
        number = record.current_version + 1
        self._add(dataset_id, number, version)
        record = replace(
            record, current_version=number, row_count=version.row_count, column_count=version.column_count,
            sheet_name=version.sheet_name or record.sheet_name, updated_at=self._now(),
        )
        self.datasets[dataset_id] = record
        pruned = [k for k in self.versions if k[0] == dataset_id and k[1] <= number - keep_versions]
        pruned_paths = {self.versions[k].parquet_path for k in pruned}
        for key in pruned:
            del self.versions[key]
        still_used = {v.parquet_path for (d, _), v in self.versions.items() if d == dataset_id}
        return record, sorted(pruned_paths - still_used)
