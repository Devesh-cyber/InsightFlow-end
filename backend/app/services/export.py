"""
Export a version as CSV or XLSX, and generate the pandas recipe.

XLSX is written with xlsxwriter in constant-memory mode, row by row. Measured:
Polars' built-in write_excel held the whole workbook in memory (856 MB peak for
267k rows: would crash a 512 MB server); streaming keeps memory flat. It is
still CPU-bound (~15 s for 2.4M cells on a laptop), so XLSX export is capped at
MAX_XLSX_EXPORT_CELLS; CSV has no cap and is fast.
"""

import datetime
import math
import re
from pathlib import Path
from uuid import UUID

import polars as pl
import xlsxwriter

from app.core.errors import UnprocessableError
from app.engine.recipe import pandas_recipe
from app.services.analysis import AnalysisService

EXCEL_MAX_ROWS = 1_048_575  # Excel's hard sheet limit minus the header row
MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w.\- ]+", "", name).strip().replace(" ", "_")
    return (cleaned or "dataset")[:100]


def write_xlsx(df: pl.DataFrame, out: Path) -> None:
    workbook = xlsxwriter.Workbook(
        str(out),
        {
            "constant_memory": True,  # stream rows to disk instead of holding the sheet
            "strings_to_numbers": False,
            "strings_to_formulas": False,  # text like "=HYPERLINK(...)" stays text: no formula injection
            "strings_to_urls": False,
            "nan_inf_to_errors": True,
        },
    )
    sheet = workbook.add_worksheet("data")
    header = workbook.add_format({"bold": True})
    date_format = workbook.add_format({"num_format": "yyyy-mm-dd"})
    datetime_format = workbook.add_format({"num_format": "yyyy-mm-dd hh:mm:ss"})
    sheet.write_row(0, 0, df.columns, header)
    sheet.freeze_panes(1, 0)

    formats = {}
    for index, dtype in enumerate(df.dtypes):
        if dtype == pl.Date:
            formats[index] = date_format
        elif isinstance(dtype, pl.Datetime):
            formats[index] = datetime_format

    row_number = 1
    for chunk in df.iter_slices(20_000):
        for row in chunk.iter_rows():
            for col, value in enumerate(row):
                if value is None:
                    continue
                if col in formats:
                    if isinstance(value, datetime.datetime) and value.tzinfo is not None:
                        value = value.replace(tzinfo=None)
                    sheet.write_datetime(row_number, col, value, formats[col])
                elif isinstance(value, float) and not math.isfinite(value):
                    sheet.write_blank(row_number, col, None)
                elif isinstance(value, str):
                    sheet.write_string(row_number, col, value)
                else:
                    sheet.write(row_number, col, value)
            row_number += 1
    workbook.close()


class ExportService:
    def __init__(self, analysis: AnalysisService, max_xlsx_cells: int = 1_000_000) -> None:
        self.analysis = analysis
        self.max_xlsx_cells = max_xlsx_cells

    def export(self, user_id: UUID, dataset_id: UUID, fmt: str, version: int | None, workdir: Path) -> tuple[Path, str, str]:
        resolved = self.analysis.resolve(user_id, dataset_id, version)
        filename = f"{safe_filename(resolved.dataset.name)}_v{resolved.version.version}.{fmt}"
        out = workdir / filename

        if fmt == "xlsx":
            rows, cols = resolved.version.row_count, resolved.version.column_count
            if rows > EXCEL_MAX_ROWS:
                raise UnprocessableError(
                    f"Excel sheets hold at most {EXCEL_MAX_ROWS:,} rows; this version has {rows:,}. Export as CSV instead.",
                    code="too_large_for_excel",
                )
            if rows * cols > self.max_xlsx_cells:
                raise UnprocessableError(
                    f"This version has {rows * cols:,} cells; Excel export is limited to {self.max_xlsx_cells:,} "
                    "cells on this server. Export as CSV (no limit), or drop some columns first.",
                    code="too_large_for_excel",
                    details={"cells": rows * cols, "limit": self.max_xlsx_cells},
                )
            write_xlsx(self.analysis.frame(resolved), out)
        else:
            self.analysis.frame(resolved).write_csv(out)
        return out, filename, MEDIA_TYPES[fmt]

    def recipe(self, user_id: UUID, dataset_id: UUID) -> tuple[str, str]:
        resolved = self.analysis.resolve(user_id, dataset_id)
        versions = self.analysis.repo.list_versions(user_id, dataset_id)
        return pandas_recipe(resolved.dataset, versions), f"{safe_filename(resolved.dataset.name)}_recipe.py"
