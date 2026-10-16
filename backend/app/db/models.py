"""Plain data records passed between the database layer and the services."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class DatasetRecord:
    id: UUID
    user_id: UUID
    name: str
    source_filename: str
    source_format: str
    source_path: str
    source_size_bytes: int
    sheet_name: str | None
    sheet_names: list[str] | None
    current_version: int
    status: str
    row_count: int
    column_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class VersionRecord:
    dataset_id: UUID
    version: int
    parquet_path: str
    row_count: int
    column_count: int
    size_bytes: int
    operation: dict
    summary: dict | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NewDataset:
    id: UUID
    user_id: UUID
    name: str
    source_filename: str
    source_format: str
    source_path: str
    source_size_bytes: int
    sheet_name: str | None
    sheet_names: list[str] | None


@dataclass(frozen=True, slots=True)
class NewVersion:
    parquet_path: str
    row_count: int
    column_count: int
    size_bytes: int
    operation: dict
    summary: dict | None
    sheet_name: str | None = None  # set when the version changes the loaded sheet


@dataclass(frozen=True, slots=True)
class Usage:
    datasets: int
    storage_bytes: int
