"""Cleaning operations: correctness of results AND of the evidence they report."""

import datetime

import polars as pl
import pytest

from app.core.errors import UnprocessableError
from app.engine.operations import OPERATIONS, run_operation


@pytest.fixture
def df():
    return pl.DataFrame({
        "id": [1, 2, 3, 4, 5, 5],
        "age": ["25", "?", "31", None, "40", "40"],
        "city": [" Pune", "Delhi", None, "pune", "Mumbai", "Mumbai"],
        "salary": [50000, 62000, None, 58000, 1_000_000, 1_000_000],
        "price": ["₹1,200", "₹950", "₹10", "₹1,20,000", "₹5", "₹5"],
        "joined": ["25/04/2024", "03/05/2024", "13/01/2024", None, "01/02/2024", "01/02/2024"],
        "active": ["yes", "no", "YES", "no", None, None],
    })


def run(df, op, **params):
    return run_operation(df, op, params)


def test_every_operation_has_a_description():
    assert len(OPERATIONS) == 15


def test_convert_to_integer_with_markers(df):
    r = run(df, "convert_type", column="age", to="integer", treat_as_missing=["?"])
    assert r.df["age"].to_list() == [25, None, 31, None, 40, 40]
    assert r.effect["changed_per_column"] == {"age": 1}  # only "?" really changed; "25" -> 25 didn't
    assert r.effect["dtype_changes"] == {"age": {"from": "text", "to": "integer"}}
    assert r.samples["changed_rows"]["rows"] == [{"row": 2, "before": ["?"], "after": [None]}]


def test_convert_failures_reported_or_aborted(df):
    r = run(df, "convert_type", column="age", to="float")
    assert any("'?' (1)" in w for w in r.warnings)
    with pytest.raises(UnprocessableError) as info:
        run(df, "convert_type", column="age", to="float", on_error="abort")
    assert info.value.details == {"failures": 1}


def test_convert_dates_day_first(df):
    r = run(df, "convert_type", column="joined", to="date", format_options=["%d/%m/%Y"])
    assert r.df["joined"][0] == datetime.date(2024, 4, 25)
    assert r.df["joined"].null_count() == 1


def test_convert_boolean(df):
    r = run(df, "convert_type", column="active", to="boolean", true_values=["yes"], false_values=["no"])
    assert r.df["active"].to_list() == [True, False, True, False, None, None]


def test_parse_formatted_number_handles_indian_grouping(df):
    r = run(df, "parse_formatted_number", column="price")
    assert r.df["price"].to_list() == [1200, 950, 10, 120000, 5, 5]
    assert r.df["price"].dtype == pl.Int64


def test_drop_duplicates_and_subset(df):
    assert run(df, "drop_duplicates").effect["rows_removed"] == 1
    r = run(df, "drop_duplicates", columns=["active"], keep="first")
    assert r.df.height == 4  # yes, no, YES (different case), null
    assert [row["row"] for row in r.samples["removed_rows"]["rows"]] == [4, 6]


def test_drop_missing_rows(df):
    assert run(df, "drop_missing_rows", how="any", columns=["city"]).effect["rows_removed"] == 1
    assert run(df, "drop_missing_rows", how="all", columns=["city", "age"]).effect["rows_removed"] == 0


def test_fill_missing_strategies(df):
    assert run(df, "fill_missing", columns=["salary"], strategy="median").df["salary"][2] == 62000
    whole = run(df, "fill_missing", columns=["salary"], strategy="mean")  # mean = 434000 exactly
    assert whole.df["salary"][2] == 434000 and whole.df["salary"].dtype == pl.Int64
    fractional = run(pl.DataFrame({"n": [1, 2, None]}), "fill_missing", columns=["n"], strategy="mean")
    assert fractional.df["n"].to_list() == [1.0, 2.0, 1.5] and any("decimal" in w for w in fractional.warnings)
    assert run(df, "fill_missing", columns=["city"], strategy="constant", value="Unknown").df["city"][2] == "Unknown"
    assert run(df, "fill_missing", columns=["city"], strategy="forward").df["city"][2] == "Delhi"
    with pytest.raises(UnprocessableError):
        run(df, "fill_missing", columns=["city"], strategy="mean")
    with pytest.raises(UnprocessableError, match="valid integer"):
        run(df, "fill_missing", columns=["salary"], strategy="constant", value="abc")


def test_text_operations(df):
    assert run(df, "trim_whitespace", columns=["city"]).df["city"][0] == "Pune"
    assert run(df, "change_case", columns=["city"], case="title").df["city"][3] == "Pune"
    r = run(df, "replace_values", column="city", find="Mumbai", replace="Bombay")
    assert r.effect["cells_changed"] == 2
    assert run(df, "replace_values", column="city", find="un", replace="UN", match="contains").df["city"][3] == "pUNe"


def test_mark_missing_numeric_and_text(df):
    assert run(df, "mark_missing", columns=["salary"], values=["1000000"]).df["salary"].null_count() == 3
    assert run(df, "mark_missing", columns=["age"], values=["?"]).df["age"].null_count() == 2


def test_outliers_cap_and_remove():
    data = pl.DataFrame({"v": [10, 12, 11, 13, 12, 11, 500, None]})
    capped = run(data, "handle_outliers", column="v", action="cap")
    assert capped.df["v"].max() < 500 and capped.df["v"].dtype == pl.Int64
    assert capped.effect["changed_per_column"] == {"v": 1}
    removed = run(data, "handle_outliers", column="v", action="remove")
    assert removed.effect["rows_removed"] == 1 and removed.df["v"].null_count() == 1  # missing values kept
    z = run(data, "handle_outliers", column="v", method="zscore", factor=2, action="remove")
    assert z.effect["rows_removed"] == 1


def test_drop_rename_and_structure(df):
    r = run(df, "rename_columns", mapping={"id": "customer_id"})
    assert r.df.columns[0] == "customer_id" and r.effect["columns_renamed"] == {"id": "customer_id"}
    assert r.effect["cells_changed"] == 0
    with pytest.raises(UnprocessableError, match="already in use"):
        run(df, "rename_columns", mapping={"id": "age"})
    assert run(df, "drop_columns", columns=["price"]).effect["columns_removed"] == ["price"]
    with pytest.raises(UnprocessableError, match="every column"):
        run(df, "drop_columns", columns=df.columns)


def test_round_numbers():
    data = pl.DataFrame({"v": [1.2345, 2.5555], "n": [1, 2]})
    r = run(data, "round_numbers", columns=["v", "n"], decimals=2)
    assert r.df["v"].to_list() == [1.23, 2.56] and any("Skipped" in w for w in r.warnings)


def test_no_change_is_flagged():
    data = pl.DataFrame({"a": [1, 2]})
    assert run(data, "drop_duplicates").effect["no_change"] is True


@pytest.mark.parametrize(
    "op,params",
    [("nope", {}), ("drop_columns", {}), ("drop_columns", {"columns": ["missing"]}),
     ("fill_missing", {"columns": ["age"], "strategy": "magic"}), ("drop_columns", {"columns": ["id"], "extra": 1})],
)
def test_invalid_requests(df, op, params):
    with pytest.raises(UnprocessableError):
        run_operation(df, op, params)


def test_row_id_never_leaks(df):
    for op, params in [("drop_duplicates", {}), ("fill_missing", {"columns": ["city"], "strategy": "mode"})]:
        assert "__insightflow_row_id__" not in run_operation(df, op, params).df.columns
