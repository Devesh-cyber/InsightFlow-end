"""Builders for test files."""

import datetime
import io
import zipfile

import xlsxwriter


def make_xlsx(sheets: dict[str, list[list]], date_cells: bool = True) -> bytes:
    """sheets = {"Sheet name": [[header...], [row...], ...]}. datetime values get a date format."""
    buffer = io.BytesIO()
    workbook = xlsxwriter.Workbook(buffer, {"in_memory": True})
    date_format = workbook.add_format({"num_format": "yyyy-mm-dd"})
    for name, rows in sheets.items():
        sheet = workbook.add_worksheet(name)
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                if value is None:
                    continue
                if isinstance(value, datetime.datetime) and date_cells:
                    sheet.write_datetime(r, c, value, date_format)
                elif isinstance(value, str):
                    sheet.write_string(r, c, value)
                else:
                    sheet.write(r, c, value)
    workbook.close()
    return buffer.getvalue()


def make_zip_bomb() -> bytes:
    """A small .xlsx-looking zip that expands enormously."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", "<workbook/>")
        archive.writestr("xl/worksheets/sheet1.xml", b"0" * (50 * 1024 * 1024))
    return buffer.getvalue()


MESSY_CSV = (
    "Customer ID,Zip Code,Age,Signup Date,Salary,City\n"
    "1,00123,25,2024-01-05,50000,Mumbai\n"
    "2,04567,?,2024-02-10,62000,Pune\n"
    "3,00999,31,2024-03-15,-,Delhi\n"
    "4,01234,N/A,2024-04-20,58000,\n"
    "5,00123,25,2024-01-05,50000,Mumbai\n"
    "6,00777,42,2024-05-02,71000,Pune\n"
    "7,00888,38,2024-06-11,?,Delhi\n"
).encode()
