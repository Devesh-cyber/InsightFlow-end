import datetime

import pytest

from app.core.errors import UnprocessableError, UnsupportedMediaTypeError
from app.engine.readers import clean_column_names, detect_format, list_sheets, read_table
from tests.helpers import MESSY_CSV, make_xlsx, make_zip_bomb


def write(tmp_path, name: str, data: bytes):
    path = tmp_path / name
    path.write_bytes(data)
    return path


# ------------------------------------------------------------------ formats
@pytest.mark.parametrize("name,fmt", [("a.csv", "csv"), ("A.CSV", "csv"), ("a.tsv", "csv"), ("a.txt", "csv"), ("a.xlsx", "xlsx")])
def test_detect_format(name, fmt):
    assert detect_format(name) == fmt


@pytest.mark.parametrize("name", ["a.xls", "a.xlsm", "a.json", "a.exe", "noextension"])
def test_detect_format_rejects(name):
    with pytest.raises(UnsupportedMediaTypeError):
        detect_format(name)


def test_xls_message_is_helpful():
    with pytest.raises(UnsupportedMediaTypeError, match="save as .xlsx"):
        detect_format("old.xls")


# ---------------------------------------------------------------------- CSV
def test_reads_everything_as_text(tmp_path):
    table = read_table(write(tmp_path, "m.csv", MESSY_CSV), "csv", filename="m.csv")
    assert table.df.columns == ["Customer ID", "Zip Code", "Age", "Signup Date", "Salary", "City"]
    assert table.df.height == 7
    assert table.df["Zip Code"][0] == "00123"  # nothing converted yet


def test_semicolon_delimiter_detected(tmp_path):
    table = read_table(write(tmp_path, "s.csv", b"a;b;c\n1;2;3\n4;5;6\n"), "csv", filename="s.csv")
    assert table.source.delimiter == ";"
    assert table.df.columns == ["a", "b", "c"]


def test_tab_delimiter_for_tsv(tmp_path):
    table = read_table(write(tmp_path, "t.tsv", b"a\tb\n1\t2\n"), "csv", filename="t.tsv")
    assert table.df.columns == ["a", "b"]


def test_latin1_file_is_decoded(tmp_path):
    data = "name,city\nJosé,Montréal\n".encode("latin-1")
    table = read_table(write(tmp_path, "l.csv", data), "csv", filename="l.csv")
    assert table.source.encoding in ("cp1252", "latin-1")
    assert table.df["city"][0] == "Montréal"


def test_utf8_bom_is_stripped(tmp_path):
    table = read_table(write(tmp_path, "b.csv", "﻿name,age\nA,1\n".encode()), "csv", filename="b.csv")
    assert table.df.columns == ["name", "age"]
    assert table.source.encoding == "utf-8-sig"


def test_utf16_file_is_decoded(tmp_path):
    table = read_table(write(tmp_path, "u.csv", "name,age\nZoë,3\n".encode("utf-16")), "csv", filename="u.csv")
    assert table.df["name"][0] == "Zoë"


def test_binary_garbage_rejected(tmp_path):
    with pytest.raises(UnsupportedMediaTypeError):
        read_table(write(tmp_path, "x.csv", b"\x89PNG\r\n\x1a\n\x00\x00\x00"), "csv", filename="x.csv")


@pytest.mark.parametrize("data", [b"", b"   \n\n"])
def test_empty_file_rejected(tmp_path, data):
    with pytest.raises(UnprocessableError, match="empty"):
        read_table(write(tmp_path, "e.csv", data), "csv", filename="e.csv")


def test_header_only_rejected(tmp_path):
    with pytest.raises(UnprocessableError, match="no data rows"):
        read_table(write(tmp_path, "h.csv", b"a,b,c\n"), "csv", filename="h.csv")


def test_ragged_rows_give_clear_error(tmp_path):
    with pytest.raises(UnprocessableError, match="more values than the header"):
        read_table(write(tmp_path, "r.csv", b"a,b\n1,2\n3,4,5\n"), "csv", filename="r.csv")


def test_blank_lines_dropped_and_counted(tmp_path):
    table = read_table(write(tmp_path, "b.csv", b"a,b\n1,2\n\n,\n3,4\n"), "csv", filename="b.csv")
    assert table.df.height == 2
    assert table.dropped_empty_rows == 2


def test_trailing_empty_columns_dropped(tmp_path):
    table = read_table(write(tmp_path, "t.csv", b"a,b,,\n1,2,,\n3,4,,\n"), "csv", filename="t.csv")
    assert table.df.columns == ["a", "b"]
    assert len(table.dropped_empty_columns) == 2


def test_header_cleanup():
    names, renamed = clean_column_names([" Name ", "", "Age", "Age", None, "Age"])
    assert names == ["Name", "column_2", "Age", "Age_2", "column_5", "Age_3"]
    assert {r["reason"] for r in renamed} == {"surrounding spaces removed", "blank header", "duplicate header"}


# --------------------------------------------------------------------- XLSX
def test_xlsx_lists_sheets_and_reads_first(tmp_path):
    data = make_xlsx({"Sales": [["id", "amount"], [1, 10.5]], "Costs": [["id", "cost"], [1, 3]]})
    path = write(tmp_path, "w.xlsx", data)
    assert list_sheets(path) == ["Sales", "Costs"]
    table = read_table(path, "xlsx")
    assert table.source.sheet == "Sales"
    assert table.source.sheet_names == ["Sales", "Costs"]
    assert table.df.columns == ["id", "amount"]


def test_xlsx_select_other_sheet(tmp_path):
    data = make_xlsx({"Sales": [["id"], [1]], "Costs": [["cost"], [3]]})
    table = read_table(write(tmp_path, "w.xlsx", data), "xlsx", sheet="Costs")
    assert table.df.columns == ["cost"]


def test_xlsx_unknown_sheet_lists_options(tmp_path):
    data = make_xlsx({"Sales": [["id"], [1]]})
    with pytest.raises(UnprocessableError) as info:
        read_table(write(tmp_path, "w.xlsx", data), "xlsx", sheet="Nope")
    assert info.value.details == {"sheet_names": ["Sales"]}


def test_xlsx_mixed_column_after_many_rows_keeps_every_row(tmp_path):
    """
    Regression for a real Polars behaviour: when the type is guessed from the
    first rows only, a later "?" becomes null and its row is dropped.
    We read every cell as text, so nothing is lost.
    """
    rows = [["n", "when"]]
    rows += [[float(i), datetime.datetime(2024, 1, 1)] for i in range(1, 3001)]
    rows.append(["?", "unknown"])
    table = read_table(write(tmp_path, "big.xlsx", make_xlsx({"S": rows})), "xlsx")
    assert table.df.height == 3001
    assert table.df["n"][-1] == "?"


def test_empty_sheet_is_clear_error(tmp_path):
    data = make_xlsx({"Data": [["a"], [1]], "Blank": []})
    with pytest.raises(UnprocessableError, match="empty"):
        read_table(write(tmp_path, "w.xlsx", data), "xlsx", sheet="Blank")


def test_xls_disguised_as_xlsx_rejected(tmp_path):
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100
    with pytest.raises(UnsupportedMediaTypeError, match="password"):
        read_table(write(tmp_path, "fake.xlsx", ole), "xlsx")


def test_csv_renamed_to_xlsx_rejected(tmp_path):
    with pytest.raises(UnsupportedMediaTypeError):
        read_table(write(tmp_path, "fake.xlsx", MESSY_CSV), "xlsx")


def test_zip_bomb_rejected(tmp_path):
    with pytest.raises(UnprocessableError, match="too much data"):
        read_table(write(tmp_path, "bomb.xlsx", make_zip_bomb()), "xlsx")
