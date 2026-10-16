"""
Cleaning, versions and undo.

preview -> runs the operation on the current version, returns evidence, writes nothing
apply   -> same computation, then a NEW version (Storage first, then DB, version-checked)
revert  -> a new version that points at an older version's file (nothing copied)

Every change is a version, so nothing is ever destroyed: any step can be undone
until it ages out of the retention window (MAX_VERSIONS_PER_DATASET).
"""

import logging
from pathlib import Path
from uuid import UUID

from app.core.errors import NotFoundError, UnprocessableError
from app.db.models import DatasetRecord, NewVersion, VersionRecord
from app.db.repository import VersionConflictError
from app.engine.operations import column_info, run_operation
from app.engine.quality import health_report
from app.services.analysis import AnalysisService
from app.services.datasets import DatasetService, parquet_path, write_parquet

logger = logging.getLogger("app.services.cleaning")


def sheet_of(version: VersionRecord) -> str | None:
    summary = version.summary or {}
    return summary.get("sheet") or (summary.get("source") or {}).get("sheet")


def version_view(version: VersionRecord, current: int) -> dict:
    summary = version.summary or {}
    op = version.operation or {}
    description = summary.get("description") or {
        "upload": "Uploaded the file",
        "select_sheet": f"Loaded sheet '{(op.get('params') or {}).get('sheet')}'",
    }.get(op.get("type"), op.get("type", ""))
    effect = summary.get("effect") or {}
    return {
        "version": version.version,
        "is_current": version.version == current,
        "operation": op,
        "description": description,
        "rows": version.row_count,
        "columns": version.column_count,
        "rows_removed": effect.get("rows_removed"),
        "cells_changed": effect.get("cells_changed"),
        "warnings": summary.get("warnings", []),
        "created_at": version.created_at.isoformat(),
    }


class CleaningService:
    def __init__(self, analysis: AnalysisService, datasets: DatasetService) -> None:
        self.analysis = analysis
        self.datasets = datasets

    def recommendations(self, user_id: UUID, dataset_id: UUID) -> dict:
        resolved = self.analysis.resolve(user_id, dataset_id)
        report = self.analysis.cached(resolved, "health", None, lambda: health_report(self.analysis.frame(resolved)))
        return {"version": resolved.version.version, "issues": report["issues"]}

    def preview(self, user_id: UUID, dataset_id: UUID, op_type: str, params: dict | None) -> dict:
        resolved = self.analysis.resolve(user_id, dataset_id)
        result = run_operation(self.analysis.frame(resolved), op_type, params, with_samples=True)
        return {
            "version": resolved.version.version,
            "operation": result.operation,
            "description": result.description,
            "effect": result.effect,
            "warnings": result.warnings,
            "samples": result.samples,
            "columns_after": column_info(result.df),
        }

    def apply(
        self, user_id: UUID, dataset_id: UUID, op_type: str, params: dict | None, expected_version: int, workdir: Path
    ) -> tuple[DatasetRecord, VersionRecord, dict]:
        resolved = self.analysis.resolve(user_id, dataset_id)
        if resolved.dataset.current_version != expected_version:
            raise VersionConflictError(resolved.dataset.current_version)  # fail fast; re-checked atomically below

        result = run_operation(self.analysis.frame(resolved), op_type, params, with_samples=False)
        if result.effect["no_change"]:
            raise UnprocessableError("This operation wouldn't change anything.", code="no_change",
                                     details={"warnings": result.warnings})
        if result.df.height == 0:
            raise UnprocessableError("This operation would remove every row.", code="would_empty_dataset")

        local, size = write_parquet(result.df, workdir)
        new_path = parquet_path(user_id, dataset_id, expected_version + 1)
        self.datasets.storage.upload(new_path, local, "application/vnd.apache.parquet")
        try:
            dataset, orphaned = self.datasets.repo.add_version(
                user_id,
                dataset_id,
                expected_version,
                NewVersion(
                    parquet_path=new_path,
                    row_count=result.df.height,
                    column_count=result.df.width,
                    size_bytes=size,
                    operation=result.operation,
                    summary={
                        "description": result.description,
                        "effect": result.effect,
                        "warnings": result.warnings,
                        "column_info": column_info(result.df),
                        "sheet": resolved.dataset.sheet_name,
                    },
                ),
                self.datasets.limits.keep_versions,
            )
        except Exception:
            self.datasets._cleanup([new_path])
            raise
        self.datasets._cleanup(orphaned)
        logger.info("applied %s to %s -> v%s", op_type, dataset_id, dataset.current_version)
        record, version = self.datasets.get(user_id, dataset_id)
        return record, version, {"description": result.description, "effect": result.effect, "warnings": result.warnings}

    def versions(self, user_id: UUID, dataset_id: UUID) -> dict:
        dataset = self.datasets.repo.get_dataset(user_id, dataset_id)
        if dataset is None:
            raise NotFoundError("Dataset not found.", code="dataset_not_found")
        records = self.datasets.repo.list_versions(user_id, dataset_id)
        return {
            "current_version": dataset.current_version,
            "retention": self.datasets.limits.keep_versions,
            "versions": [version_view(v, dataset.current_version) for v in records],
        }

    def revert(self, user_id: UUID, dataset_id: UUID, to_version: int, expected_version: int) -> tuple[DatasetRecord, VersionRecord]:
        resolved = self.analysis.resolve(user_id, dataset_id)
        if to_version == resolved.dataset.current_version:
            raise UnprocessableError("That's already the current version.", code="no_change")
        target = self.datasets.repo.get_version(user_id, dataset_id, to_version)
        if target is None:
            raise NotFoundError(f"Version {to_version} doesn't exist (it may have been pruned).", code="version_not_found")

        summary = dict(target.summary or {})
        summary.update({"description": f"Reverted to version {to_version}", "reverted_to": to_version, "sheet": sheet_of(target)})
        summary.pop("effect", None)
        _, orphaned = self.datasets.repo.add_version(
            user_id,
            dataset_id,
            expected_version,
            NewVersion(
                parquet_path=target.parquet_path,  # reuse the file: revert copies nothing
                row_count=target.row_count,
                column_count=target.column_count,
                size_bytes=target.size_bytes,
                operation={"type": "revert", "params": {"to_version": to_version}},
                summary=summary,
                sheet_name=sheet_of(target),
            ),
            self.datasets.limits.keep_versions,
        )
        self.datasets._cleanup(orphaned)
        return self.datasets.get(user_id, dataset_id)
