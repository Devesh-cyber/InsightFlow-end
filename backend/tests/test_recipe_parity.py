"""The 'Reproduce it' script must give exactly what InsightFlow gives. Runs real pandas."""

from pathlib import Path

import pytest

pytest.importorskip("pandas")

from tests.recipe_harness import assert_same, run_both  # noqa: E402

ANOMALY = (Path(__file__).parent / "data" / "anomaly.csv").read_bytes()

# The exact 14 steps from the user's audit (InsightFlow History of v15).
AUDIT_STEPS = [
    ("drop_duplicates", {"columns": None, "keep": "first"}),
    ("convert_type", {"column": "age", "to": "float", "treat_as_missing": ["unknown"]}),
    ("fill_missing", {"columns": ["age"], "strategy": "median"}),
    ("fill_missing", {"columns": ["city"], "strategy": "mode"}),
    ("fill_missing", {"columns": ["customer_email"], "strategy": "constant", "value": "Unknown"}),
    ("fill_missing", {"columns": ["customer_name"], "strategy": "constant", "value": "Unknown"}),
    ("fill_missing", {"columns": ["product_category"], "strategy": "mode"}),
    ("handle_outliers", {"column": "age", "method": "iqr", "factor": 1.5, "action": "cap"}),
    ("handle_outliers", {"column": "total_amount", "method": "iqr", "factor": 1.5, "action": "cap"}),
    ("handle_outliers", {"column": "unit_price", "method": "iqr", "factor": 1.5, "action": "cap"}),
    ("trim_whitespace", {"columns": ["city"]}),
    ("trim_whitespace", {"columns": ["product_category"]}),
    ("handle_outliers", {"column": "units", "method": "iqr", "factor": 1.5, "action": "cap"}),
]


def test_audit_steps_reproduce_exactly():
    df, rows, code = run_both(ANOMALY, AUDIT_STEPS)
    assert_same(df, rows, code)
    units = {r["order_id"]: r["units"] for r in rows}
    assert units["ORD-0066"] in ("10", "10.0")  # integer cap, not 10.75


RULE_STEPS = [
    ("convert_type", {"column": "age", "to": "float", "treat_as_missing": ["unknown"]}),
    ("apply_rule", {"conditions": [{"column": "age", "op": "lt", "value": 0}], "action": "set_missing"}),
    ("apply_rule", {"conditions": [{"column": "age", "op": "not_between", "value": 0, "value2": 120}], "action": "set_missing"}),
    ("apply_rule", {"conditions": [{"column": "units", "op": "le", "value": 0}], "action": "remove_rows"}),
    ("apply_rule", {"conditions": [{"column": "unit_price", "op": "lt", "value": 0}, {"column": "units", "op": "gt", "value": 9000}],
                    "combine": "or", "action": "remove_rows"}),
    ("apply_rule", {"conditions": [{"column": "total_amount", "op": "differs_from",
                                    "formula": [{"column": "units"}, "*", {"column": "unit_price"}]}],
                    "action": "calculate", "target": "total_amount", "formula": [{"column": "units"}, "*", {"column": "unit_price"}]}),
    ("apply_rule", {"conditions": [{"column": "customer_email", "op": "invalid_email"}], "action": "set_missing"}),
    ("apply_rule", {"conditions": [{"column": "order_date", "op": "invalid_date"}], "action": "set_missing"}),
    ("trim_whitespace", {"columns": ["city", "product_category"]}),
    ("apply_rule", {"conditions": [{"column": "city", "op": "eq", "value": "mumbai", "ignore_case": True}], "action": "set_value", "value": "Mumbai"}),
    ("apply_rule", {"conditions": [{"column": "order_id", "op": "duplicate"}], "action": "keep_rows"}),
    ("apply_rule", {"action": "calculate", "target": "price_per_unit_check", "formula": [{"column": "total_amount"}, "/", {"column": "units"}]}),
    ("apply_rule", {"action": "clamp", "target": "units", "min": 1, "max": 6.5}),
    ("apply_rule", {"conditions": [{"column": "city", "op": "in", "value": ["Pune", "Delhi"]}], "action": "set_value", "value": "West/North"}),
    ("apply_rule", {"conditions": [{"column": "customer_name", "op": "starts_with", "value": "customer_1", "ignore_case": True}],
                    "action": "set_missing", "target": "customer_name"}),
]


def test_rule_steps_reproduce_exactly():
    df, rows, code = run_both(ANOMALY, RULE_STEPS)
    assert_same(df, rows, code)


@pytest.mark.parametrize("op,extra", [
    ("lt", {"value": 40}), ("le", {"value": 40}), ("gt", {"value": 40}), ("ge", {"value": 40}), ("eq", {"value": 46}),
    ("ne", {"value": 46}), ("between", {"value": 60, "value2": 30}), ("not_between", {"value": 30, "value2": 60}),
    ("is_empty", {}), ("not_empty", {}), ("duplicate", {}),
])
def test_each_numeric_operator(op, extra):
    steps = [("convert_type", {"column": "age", "to": "float", "treat_as_missing": ["unknown"]}),
             ("apply_rule", {"conditions": [{"column": "age", "op": op, **extra}], "action": "set_value", "target": "units", "value": 99})]
    df, rows, code = run_both(ANOMALY, steps)
    assert_same(df, rows, code)


@pytest.mark.parametrize("op,extra", [
    ("eq", {"value": "Mumbai"}), ("ne", {"value": "Mumbai"}), ("eq", {"value": "MUMBAI", "ignore_case": True}),
    ("contains", {"value": "mum", "ignore_case": True}), ("not_contains", {"value": "a"}), ("starts_with", {"value": "P"}),
    ("ends_with", {"value": "i"}), ("in", {"value": ["Delhi", "Pune"]}), ("not_in", {"value": ["Delhi", "Pune"]}),
    ("is_empty", {}), ("not_empty", {}), ("duplicate", {"ignore_case": True}),
])
def test_each_text_operator(op, extra):
    steps = [("apply_rule", {"conditions": [{"column": "city", "op": op, **extra}], "action": "set_value", "target": "product_category", "value": "X"})]
    df, rows, code = run_both(ANOMALY, steps)
    assert_same(df, rows, code)


def test_formula_precedence_and_division():
    steps = [("apply_rule", {"action": "calculate", "target": "f",
                             "formula": [{"column": "units"}, "+", {"column": "unit_price"}, "*", {"number": 2}, "-",
                                         {"column": "total_amount"}, "/", {"column": "units"}]})]
    df, rows, code = run_both(ANOMALY, steps)
    assert_same(df, rows, code)
    row = next(r for r in rows if r["order_id"] == "ORD-0032")  # units = 0 -> division gives missing
    assert row["f"] == ""


def test_standardize_and_mark_missing_with_spaces():
    csv = b"city,code\nMumbai,1\n mumbai ,2\nMUMBAI,3\n  ?  ,4\nPune,5\n"
    steps = [("mark_missing", {"columns": ["city"], "values": ["?"]}),
             ("standardize_values", {"column": "city", "mapping": {" mumbai ": "Mumbai", "MUMBAI": "Mumbai"}})]
    df, rows, code = run_both(csv, steps)
    assert_same(df, rows, code)
    assert [r["city"] for r in rows] == ["Mumbai", "Mumbai", "Mumbai", "", "Pune"]


def test_calculate_ignores_float_noise_and_matches_pandas():
    import polars as pl
    from app.engine.operations import run_operation
    df = pl.DataFrame({"units": [3, 2, 7], "price": [1836.23, 0.1, 14503.62], "total": [5508.69, 0.2, 101525.34]})
    op = {"conditions": [], "action": "calculate", "target": "total",
          "formula": [{"column": "units"}, "*", {"column": "price"}]}
    assert run_operation(df, "apply_rule", op).effect["no_change"]  # every total was already right
    csv = b"units,price,total\n3,1836.23,1\n2,0.1,0.2\n7,14503.62,101525.34\n"
    _df, _rows, _code = run_both(csv, [("apply_rule", op)], "calc_noise")
    assert_same(_df, _rows, _code)


def test_extra_spaces_rule_matches_pandas():
    csv = b"city,n\nMumbai,1\n mumbai,2\nPune ,3\nDelhi,4\n"
    op = {"conditions": [{"column": "city", "op": "extra_spaces"}], "action": "remove_rows"}
    df, rows, code = run_both(csv, [("apply_rule", op)], "spaces")
    assert df.height == 2
    assert_same(df, rows, code)
