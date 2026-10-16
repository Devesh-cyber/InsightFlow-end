"""
Read an uploaded file (CSV or XLSX) into a DataFrame of raw text.

Everything is read as text on purpose. Type detection happens afterwards, in
`infer.py`, with the same rules for CSV and Excel. Letting each reader guess
types on its own caused two bugs we saw while designing this:

- The old pandas backend turned "00123" into 123 and "?" into a text column.
- Polars' Excel reader, when it samples only the first N rows to guess a type,
  silently turned a later "?" into null and then DROPPED that row entirely.

Safety checks done here, before any parsing:
- extension allow-list (.csv, .tsv, .txt, .xlsx)
- XLSX must really be a zip with a workbook inside, and must not be a zip bomb
- CSV must look like text (no NUL bytes), encoding is detected and normalised
  to UTF-8 so the rest of the system only ever sees UTF-8
"""

import codecs
import csv
import logging
import os
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import fastexcel
import polars as pl

from app.core.errors import UnprocessableError, UnsupportedMediaTypeError

logger = logging.getLogger("app.engine.readers")

CSV_EXTENSIONS = {".csv", ".tsv", ".txt"}
XLSX_EXTENSIONS = {".xlsx"}
REJECTED_EXTENSIONS = {
    ".xls": "The old .xls format isn't supported. Open it in Excel and save as .xlsx.",
    ".xlsm": "Macro-enabled workbooks (.xlsm) aren't supported. Save as .xlsx.",
    ".xlsb": "Binary workbooks (.xlsb) aren't supported. Save as .xlsx.",
    ".ods": "OpenDocument spreadsheets aren't supported. Save as .xlsx or .csv.",
}

MAX_COLUMNS = 1000
XLSX_MAX_UNCOMPRESSED_BYTES = 300 * 1024 * 1024  # zip-bomb guard
XLSX_MAX_COMPRESSION_RATIO = 150
XLSX_MAX_ENTRIES = 10_000
SNIFF_BYTES = 64 * 1024
CHUNK = 1024 * 1024

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # .xls and password-protected .xlsx
ZIP_MAGIC = b"PK\x03\x04"


@dataclass
class SourceInfo:
    format: str  # "csv" | "xlsx"
    encoding: str | None = None
    delimiter: str | None = None
    sheet: str | None = None
    sheet_names: list[str] | None = None

    def as_dict(self) -> dict:
        return {
            "format": self.format,
            "encoding": self.encoding,
            "delimiter": self.delimiter,
            "sheet": self.sheet,
            "sheet_names": self.sheet_names,
        }


@dataclass
class RawTable:
    """All-text table plus what we learned while reading it."""

    df: pl.DataFrame
    source: SourceInfo
    renamed_columns: list[dict] = field(default_factory=list)
    dropped_empty_rows: int = 0
    dropped_empty_columns: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------
def detect_format(filename: str) -> str:
    extension = Path(filename).suffix.lower()
    if extension in CSV_EXTENSIONS:
        return "csv"
    if extension in XLSX_EXTENSIONS:
        return "xlsx"
    if extension in REJECTED_EXTENSIONS:
        raise UnsupportedMediaTypeError(REJECTED_EXTENSIONS[extension], code="unsupported_file_type")
    raise UnsupportedMediaTypeError(
        "Only CSV (.csv, .tsv, .txt) and Excel (.xlsx) files are supported.",
        code="unsupported_file_type",
    )


def read_table(path: Path, fmt: str, *, sheet: str | None = None, filename: str = "") -> RawTable:
    if fmt == "csv":
        return _read_csv(path, filename)
    if fmt == "xlsx":
        return _read_xlsx(path, sheet)
    raise UnsupportedMediaTypeError(code="unsupported_file_type")


def list_sheets(path: Path) -> list[str]:
    _validate_xlsx_container(path)
    try:
        return list(fastexcel.read_excel(str(path)).sheet_names)
    except Exception as exc:  # calamine raises several error types
        raise UnprocessableError("This Excel file could not be read.", code="unreadable_file") from exc


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def _read_csv(path: Path, filename: str) -> RawTable:
    with open(path, "rb") as handle:
        head = handle.read(SNIFF_BYTES)

    if not head.strip():
        raise UnprocessableError("The file is empty.", code="empty_file")

    utf8_path, encoding = _normalise_to_utf8(path, head)
    try:
        delimiter = "\t" if filename.lower().endswith(".tsv") else _sniff_delimiter(utf8_path)
        try:
            df = pl.read_csv(
                utf8_path,
                has_header=False,
                infer_schema=False,  # everything as text; infer.py decides types
                separator=delimiter,
                quote_char='"',
                encoding="utf8",
                empty_string_is_null=True,
                raise_if_empty=False,
            )
        except pl.exceptions.ComputeError as exc:
            message = str(exc).split("\n")[0]
            if "more fields" in message:
                message = (
                    "Some rows have more values than the header row. "
                    "Check for unquoted commas inside values."
                )
            raise UnprocessableError(f"Could not parse the CSV: {message}", code="unreadable_file") from exc
    finally:
        if utf8_path != path:
            utf8_path.unlink(missing_ok=True)

    table = _finish(df, SourceInfo(format="csv", encoding=encoding, delimiter=delimiter))
    return table


def _normalise_to_utf8(path: Path, head: bytes) -> tuple[Path, str]:
    """Return (path_to_utf8_file, detected_encoding). Transcodes only if needed."""
    if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return _transcode(path, "utf-16"), "utf-16"

    if b"\x00" in head[:8192]:
        raise UnsupportedMediaTypeError(
            "This file doesn't look like a text CSV.", code="unsupported_file_type"
        )

    if _is_valid_utf8(path):
        return path, "utf-8-sig" if head.startswith(codecs.BOM_UTF8) else "utf-8"

    # Not UTF-8. Windows-1252 covers almost every Excel-on-Windows export;
    # Latin-1 accepts any byte, so it is the last resort.
    for encoding in ("cp1252", "latin-1"):
        try:
            return _transcode(path, encoding), encoding
        except UnicodeDecodeError:
            continue
    raise UnprocessableError("Could not detect the file's text encoding.", code="unreadable_file")


def _is_valid_utf8(path: Path) -> bool:
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        with open(path, "rb") as handle:
            while chunk := handle.read(CHUNK):
                decoder.decode(chunk)
            decoder.decode(b"", final=True)
        return True
    except UnicodeDecodeError:
        return False


def _transcode(path: Path, encoding: str) -> Path:
    fd, out_name = tempfile.mkstemp(suffix=".csv", dir=path.parent)
    out_path = Path(out_name)
    try:
        with open(path, "r", encoding=encoding, newline="") as src, os.fdopen(fd, "w", encoding="utf-8", newline="") as dst:
            while chunk := src.read(CHUNK):
                dst.write(chunk)
    except Exception:
        out_path.unlink(missing_ok=True)
        raise
    return out_path


def _sniff_delimiter(path: Path) -> str:
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
        sample = handle.read(SNIFF_BYTES)
    if "\n" in sample:
        sample = sample[: sample.rfind("\n")]  # don't sniff a half line
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------
def _validate_xlsx_container(path: Path) -> None:
    with open(path, "rb") as handle:
        magic = handle.read(8)

    if magic.startswith(OLE_MAGIC):
        raise UnsupportedMediaTypeError(
            "This looks like an old .xls file or a password-protected workbook. "
            "Remove the password and save as .xlsx.",
            code="unsupported_file_type",
        )
    if not magic.startswith(ZIP_MAGIC):
        raise UnsupportedMediaTypeError("This is not a valid .xlsx file.", code="unsupported_file_type")

    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            names = {entry.filename for entry in entries}
            compressed = sum(entry.compress_size for entry in entries) or 1
            uncompressed = sum(entry.file_size for entry in entries)
    except zipfile.BadZipFile as exc:
        raise UnsupportedMediaTypeError("This .xlsx file is damaged.", code="unsupported_file_type") from exc

    if "xl/workbook.xml" not in names:
        raise UnsupportedMediaTypeError("This is not a valid .xlsx file.", code="unsupported_file_type")
    if (
        len(entries) > XLSX_MAX_ENTRIES
        or uncompressed > XLSX_MAX_UNCOMPRESSED_BYTES
        or uncompressed / compressed > XLSX_MAX_COMPRESSION_RATIO
    ):
        raise UnprocessableError(
            "This workbook expands to too much data to process safely.", code="file_too_complex"
        )


def _read_xlsx(path: Path, sheet: str | None) -> RawTable:
    _validate_xlsx_container(path)
    try:
        reader = fastexcel.read_excel(str(path))
        sheet_names = list(reader.sheet_names)
    except Exception as exc:
        raise UnprocessableError("This Excel file could not be read.", code="unreadable_file") from exc

    if not sheet_names:
        raise UnprocessableError("The workbook has no sheets.", code="empty_file")
    chosen = sheet if sheet is not None else sheet_names[0]
    if chosen not in sheet_names:
        raise UnprocessableError(
            f"Sheet '{chosen}' does not exist.",
            code="sheet_not_found",
            details={"sheet_names": sheet_names},
        )

    try:
        df = reader.load_sheet(
            chosen,
            header_row=None,  # we read the header ourselves (see _finish)
            dtypes="string",  # every cell as text: no sampling, no silent nulls
            dtype_coercion="coerce",
        ).to_polars()
    except Exception as exc:
        if "empty" in str(exc).lower():
            raise UnprocessableError(f"Sheet '{chosen}' is empty.", code="empty_file") from exc
        raise UnprocessableError(f"Sheet '{chosen}' could not be read.", code="unreadable_file") from exc

    return _finish(df, SourceInfo(format="xlsx", sheet=chosen, sheet_names=sheet_names))


# ---------------------------------------------------------------------------
# Shared: header row, empty rows/columns, limits
# ---------------------------------------------------------------------------
def _finish(df: pl.DataFrame, source: SourceInfo) -> RawTable:
    if df.width == 0 or df.height == 0:
        raise UnprocessableError("The file is empty.", code="empty_file")

    # Normalise every column to text (calamine may hand back Null-typed columns).
    df = df.select(pl.all().cast(pl.String))

    header = [None if v is None else str(v) for v in df.row(0)]
    body = df.slice(1)

    # Drop rows where every cell is empty (blank lines, Excel formatting leftovers).
    before = body.height
    body = body.filter(~pl.all_horizontal(pl.all().is_null() | pl.all().str.contains(r"^\s*$")))
    dropped_rows = before - body.height

    # Drop columns that have no header AND no values (trailing commas, stray formatting).
    keep_idx, dropped_cols = [], []
    for idx, name in enumerate(header):
        column = body.get_column(body.columns[idx])
        empty_header = name is None or not name.strip()
        empty_values = bool(column.is_null().all()) or bool(column.drop_nulls().str.contains(r"^\s*$").all())
        if empty_header and empty_values:
            dropped_cols.append(f"column {idx + 1}")
        else:
            keep_idx.append(idx)
    body = body.select([body.columns[i] for i in keep_idx])
    header = [header[i] for i in keep_idx]

    if not header:
        raise UnprocessableError("The file has no usable columns.", code="empty_file")
    if body.height == 0:
        raise UnprocessableError("The file has a header row but no data rows.", code="empty_file")
    if len(header) > MAX_COLUMNS:
        raise UnprocessableError(
            f"The file has {len(header)} columns; the maximum is {MAX_COLUMNS}.", code="too_many_columns"
        )

    names, renamed = clean_column_names(header)
    body.columns = names
    return RawTable(
        df=body,
        source=source,
        renamed_columns=renamed,
        dropped_empty_rows=dropped_rows,
        dropped_empty_columns=dropped_cols,
    )


def clean_column_names(raw: list[str | None]) -> tuple[list[str], list[dict]]:
    """Strip whitespace, name blank headers, de-duplicate. Report every change."""
    names: list[str] = []
    renamed: list[dict] = []
    seen: set[str] = set()
    for position, original in enumerate(raw, start=1):
        name = (original or "").strip()
        reason = None
        if not name:
            name, reason = f"column_{position}", "blank header"
        elif name != original:
            reason = "surrounding spaces removed"

        if name in seen:
            base, counter = name, 2
            while f"{base}_{counter}" in seen:
                counter += 1
            name, reason = f"{base}_{counter}", "duplicate header"

        seen.add(name)
        names.append(name)
        if reason:
            renamed.append({"position": position, "from": original or "", "to": name, "reason": reason})
    return names, renamed
