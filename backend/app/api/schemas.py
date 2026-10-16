"""Response / request models: the API contract the frontend will build against."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.db.models import DatasetRecord, VersionRecord


class ColumnInfo(BaseModel):
    name: str
    dtype: str = Field(description="integer | float | boolean | date | datetime | text")
    null_count: int
    converted_from_text: bool
    missing_markers: dict[str, int] = Field(description="Missing-value markers converted to empty, with counts")


class Suggestion(BaseModel):
    id: str
    column: str
    kind: str
    message: str
    evidence: dict[str, Any]
    operation: dict[str, Any] = Field(description="The cleaning operation that would apply this (Phase 4)")


class IngestReport(BaseModel):
    source: dict[str, Any]
    rows: int
    columns: int
    renamed_columns: list[dict[str, Any]]
    dropped_empty_rows: int
    dropped_empty_columns: list[str]
    warnings: list[str]


class DatasetSummary(BaseModel):
    id: UUID
    name: str
    source_filename: str
    source_format: str
    source_size_bytes: int
    sheet_name: str | None
    sheet_names: list[str] | None
    current_version: int
    row_count: int
    column_count: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: DatasetRecord) -> "DatasetSummary":
        return cls(
            id=record.id,
            name=record.name,
            source_filename=record.source_filename,
            source_format=record.source_format,
            source_size_bytes=record.source_size_bytes,
            sheet_name=record.sheet_name,
            sheet_names=record.sheet_names,
            current_version=record.current_version,
            row_count=record.row_count,
            column_count=record.column_count,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class DatasetDetail(DatasetSummary):
    columns: list[ColumnInfo]
    suggestions: list[Suggestion]
    ingest_report: IngestReport | None
    last_operation: dict[str, Any]

    @classmethod
    def build(cls, record: DatasetRecord, version: VersionRecord) -> "DatasetDetail":
        summary = version.summary or {}
        report = None
        if "source" in summary:
            report = IngestReport(**{k: summary[k] for k in IngestReport.model_fields if k in summary})
        return cls(
            **DatasetSummary.from_record(record).model_dump(),
            columns=[ColumnInfo(**c) for c in summary.get("column_info", [])],
            suggestions=[Suggestion(**s) for s in summary.get("suggestions", [])],
            ingest_report=report,
            last_operation=version.operation,
        )


class DatasetList(BaseModel):
    datasets: list[DatasetSummary]
    limit: int


class RenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class SelectSheetRequest(BaseModel):
    sheet_name: str = Field(min_length=1, max_length=255)
    expected_version: int = Field(ge=1, description="The version you are looking at; 409 if it changed")


class SheetsResponse(BaseModel):
    sheet_names: list[str]
    current: str | None


class Usage(BaseModel):
    datasets: int
    max_datasets: int
    storage_bytes: int
    max_upload_mb: int
    max_xlsx_upload_mb: int
