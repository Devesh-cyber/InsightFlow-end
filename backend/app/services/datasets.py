"""
Dataset service: orchestrates engine + storage + database.

Write order for every change (upload, sheet switch, later: cleaning):
  1. compute the new Parquet file locally
  2. upload it to Storage under a NEW unique name (never overwrite)
  3. commit the database change (the single source of truth)
  4. if step 3 fails, delete what step 2 uploaded
So Storage and the database can never disagree about which file is current.
The worst case is an unreferenced file, never a broken dataset.

All functions here are synchronous and CPU/IO heavy: the API layer runs them
through JobLimiter (worker thread + concurrency cap).
"""

import logging
import os
import secrets
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import polars as pl

from app.core.errors import NotFoundError, UnprocessableError
from app.db.models import DatasetRecord, NewDataset, NewVersion, VersionRecord
from app.db.repository import DatasetRepository, QuotaExceededError
from app.engine.infer import infer_types
from app.engine.readers import RawTable, detect_format, read_table
from app.storage.client import ObjectStorage

logger = logging.getLogger("app.services.datasets")

CONTENT_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@dataclass(frozen=True)
class DatasetLimits:
    max_datasets: int
    keep_versions: int


def dataset_not_found() -> NotFoundError:
    return NotFoundError("Dataset not found.", code="dataset_not_found")


def clean_display_name(filename: str) -> str:
    """Filename -> safe display name (no control characters, sensible length)."""
    stem = Path(filename).stem or "dataset"
    cleaned = "".join(ch for ch in stem if unicodedata.category(ch)[0] != "C").strip()
    return (cleaned or "dataset")[:200]


def source_path(user_id: UUID, dataset_id: UUID, fmt: str) -> str:
    return f"{user_id}/{dataset_id}/source.{fmt}"


def parquet_path(user_id: UUID, dataset_id: UUID, version_hint: int) -> str:
    # Random suffix: a retried or concurrent write can never overwrite another file.
    return f"{user_id}/{dataset_id}/v{version_hint}-{secrets.token_hex(6)}.parquet"


def write_parquet(df: pl.DataFrame, directory: Path) -> tuple[Path, int]:
    fd, name = tempfile.mkstemp(suffix=".parquet", dir=directory)
    os.close(fd)
    out = Path(name)
    df.write_parquet(out, compression="zstd", statistics=True)
    return out, out.stat().st_size


def build_ingest_summary(table: RawTable, inference) -> dict:
    """The upload report shown to the user: what we read, what we changed, what we suggest."""
    return {
        "source": table.source.as_dict(),
        "rows": inference.df.height,
        "columns": inference.df.width,
        "column_info": inference.columns,
        "renamed_columns": table.renamed_columns,
        "dropped_empty_rows": table.dropped_empty_rows,
        "dropped_empty_columns": table.dropped_empty_columns,
        "suggestions": inference.suggestions,
        "warnings": inference.warnings,
    }


class DatasetService:
    def __init__(self, repo: DatasetRepository, storage: ObjectStorage, limits: DatasetLimits) -> None:
        self.repo = repo
        self.storage = storage
        self.limits = limits

    # ------------------------------------------------------------------ reads
    def get(self, user_id: UUID, dataset_id: UUID) -> tuple[DatasetRecord, VersionRecord]:
        found = self.repo.get_dataset_with_version(user_id, dataset_id)  # one round trip
        if found is None:
            raise dataset_not_found()
        dataset, version = found
        if version is None:  # should be impossible; DB constraints keep these in sync
            logger.error("dataset %s has no row for current version %s", dataset_id, dataset.current_version)
            raise dataset_not_found()
        return dataset, version

    def check_quota(self, user_id: UUID) -> None:
        """Fast early check so a doomed upload isn't parsed. Re-checked atomically on insert."""
        if self.repo.count_datasets(user_id) >= self.limits.max_datasets:
            raise QuotaExceededError(
                f"You can keep up to {self.limits.max_datasets} datasets. Delete one to upload another.",
                details={"limit": self.limits.max_datasets},
            )

    # ----------------------------------------------------------------- upload
    def ingest_upload(
        self, user_id: UUID, upload_file: Path, filename: str, size_bytes: int, sheet: str | None, workdir: Path
    ) -> tuple[DatasetRecord, VersionRecord]:
        fmt = detect_format(filename)
        if fmt == "csv" and sheet is not None:
            raise UnprocessableError("CSV files have no sheets.", code="invalid_parameter")

        table = read_table(upload_file, fmt, sheet=sheet, filename=filename)
        inference = infer_types(table.df)
        summary = build_ingest_summary(table, inference)
        local_parquet, parquet_size = write_parquet(inference.df, workdir)

        dataset_id = uuid4()
        src_path = source_path(user_id, dataset_id, fmt)
        pq_path = parquet_path(user_id, dataset_id, 1)

        uploaded: list[str] = []
        try:
            self.storage.upload(src_path, upload_file, CONTENT_TYPES[fmt])
            uploaded.append(src_path)
            self.storage.upload(pq_path, local_parquet, "application/vnd.apache.parquet")
            uploaded.append(pq_path)

            dataset = self.repo.create_dataset(
                NewDataset(
                    id=dataset_id,
                    user_id=user_id,
                    name=clean_display_name(filename),
                    source_filename=filename[:255],
                    source_format=fmt,
                    source_path=src_path,
                    source_size_bytes=size_bytes,
                    sheet_name=table.source.sheet,
                    sheet_names=table.source.sheet_names,
                ),
                NewVersion(
                    parquet_path=pq_path,
                    row_count=inference.df.height,
                    column_count=inference.df.width,
                    size_bytes=parquet_size,
                    operation={"type": "upload"},
                    summary=summary,
                ),
                self.limits.max_datasets,
            )
        except Exception:
            self._cleanup(uploaded)
            raise

        logger.info(
            "ingested dataset %s: %s rows x %s cols (%s, %s bytes)",
            dataset.id, dataset.row_count, dataset.column_count, fmt, size_bytes,
        )
        return self.get(user_id, dataset.id)

    # ----------------------------------------------------------- sheet switch
    def select_sheet(
        self, user_id: UUID, dataset_id: UUID, sheet: str, expected_version: int, workdir: Path
    ) -> tuple[DatasetRecord, VersionRecord]:
        dataset = self.repo.get_dataset(user_id, dataset_id)
        if dataset is None:
            raise dataset_not_found()
        if dataset.source_format != "xlsx":
            raise UnprocessableError("Only Excel datasets have sheets.", code="invalid_parameter")
        if dataset.sheet_names and sheet not in dataset.sheet_names:
            raise UnprocessableError(
                f"Sheet '{sheet}' does not exist.", code="sheet_not_found", details={"sheet_names": dataset.sheet_names}
            )

        local_source = workdir / "source.xlsx"
        self.storage.download(dataset.source_path, local_source)
        table = read_table(local_source, "xlsx", sheet=sheet)
        inference = infer_types(table.df)
        local_parquet, parquet_size = write_parquet(inference.df, workdir)

        pq_path = parquet_path(user_id, dataset_id, dataset.current_version + 1)
        self.storage.upload(pq_path, local_parquet, "application/vnd.apache.parquet")
        try:
            updated, orphaned = self.repo.add_version(
                user_id,
                dataset_id,
                expected_version,
                NewVersion(
                    parquet_path=pq_path,
                    row_count=inference.df.height,
                    column_count=inference.df.width,
                    size_bytes=parquet_size,
                    operation={"type": "select_sheet", "params": {"sheet": sheet}},
                    summary=build_ingest_summary(table, inference),
                    sheet_name=sheet,
                ),
                self.limits.keep_versions,
            )
        except Exception:
            self._cleanup([pq_path])
            raise
        self._cleanup(orphaned)
        return self.get(user_id, updated.id)

    # --------------------------------------------------------- rename / delete
    def rename(self, user_id: UUID, dataset_id: UUID, name: str) -> tuple[DatasetRecord, VersionRecord]:
        cleaned = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip()
        if not cleaned:
            raise UnprocessableError("Name cannot be empty.", code="invalid_parameter")
        if self.repo.rename_dataset(user_id, dataset_id, cleaned[:200]) is None:
            raise dataset_not_found()
        return self.get(user_id, dataset_id)

    def delete(self, user_id: UUID, dataset_id: UUID) -> None:
        paths = self.repo.delete_dataset(user_id, dataset_id)
        if paths is None:
            raise dataset_not_found()
        # The dataset is already gone for the user. File removal is best-effort;
        # failures are logged so leftover files can be cleaned up later.
        self._cleanup(paths)

    # ------------------------------------------------------------------ utils
    def _cleanup(self, paths: list[str]) -> None:
        if not paths:
            return
        try:
            self.storage.delete(paths)
        except Exception:
            logger.exception("could not delete %d storage object(s): %s", len(paths), paths)
