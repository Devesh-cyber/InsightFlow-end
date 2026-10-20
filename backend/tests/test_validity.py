"""
Generic validity checks: they must find injected problems in MANY random datasets
(not just the anomaly file) and stay quiet on clean ones.
"""

import random
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from app.engine.operations import run_operation
from app.engine.quality import find_issues, health_report
from app.engine.validity import find_validity_issues, relationship_damage

ANOMALY = Path(__file__).parent / "data" / "anomaly.csv"
CITIES = ["Mumbai", "Delhi", "Pune", "Chennai", "Kolkata", "Jaipur"]
CATEGORIES = ["Books", "Home", "Toys", "Garden"]


def make_clean(seed: int, rows: int) -> pl.DataFrame:
    rnd = random.Random(seed)
    start = date(2024, 1, 1)
    qty = [rnd.randint(1, 9) for _ in range(rows)]
    price = [round(rnd.uniform(5, 900), 2) for _ in range(rows)]
    fee = [round(rnd.uniform(0, 50), 2) for _ in range(rows)]
    prefix = rnd.choice(["INV", "TX", "SKU", "A"])
    return pl.DataFrame({
        "ref": [f"{prefix}-{i:05d}" for i in range(rows)],
        "city": [rnd.choice(CITIES) for _ in range(rows)],
        "category": [rnd.choice(CATEGORIES) for _ in range(rows)],
        "qty": qty,
        "price": price,
        "fee": fee,
        "subtotal": [round(q * p, 2) for q, p in zip(qty, price)],
        "grand": [round(q * p + f, 2) for q, p, f in zip(qty, price, fee)],
        "day": [(start + timedelta(days=rnd.randint(0, 600))).isoformat() for _ in range(rows)],
        "email": [f"user{i}@mail{rnd.randint(1, 5)}.com" for i in range(rows)],
        # a genuinely signed column (profit/loss): plenty of negatives, so none of them is "suspicious"
        "change": [round(rnd.gauss(0, 20), 1) for _ in range(rows)],
    })


def inject(df: pl.DataFrame, seed: int) -> tuple[pl.DataFrame, dict]:
    """Break a few cells in known places; return what a good checker must report."""
    rnd = random.Random(seed * 7 + 1)
    rows = df.to_dicts()
    picks = rnd.sample(range(len(rows)), 8)
    dup, neg, zero, bad_total, bad_email, variant = picks[:6]
    rows[dup]["ref"] = rows[picks[6]]["ref"]
    rows[neg]["price"] = -abs(rows[neg]["price"])
    rows[zero]["qty"] = 0
    rows[bad_total]["subtotal"] = round(rows[bad_total]["subtotal"] + 123.45, 2)
    rows[bad_email]["email"] = "not-an-email"
    rows[variant]["city"] = rows[variant]["city"].lower() + " "
    broken = pl.DataFrame(rows, schema=df.schema)
    return broken, {"ref", "price", "qty", "subtotal", "email", "city"}


SEEDS = list(range(40))


@pytest.mark.parametrize("seed", SEEDS)
def test_clean_random_datasets_raise_no_validity_alarms(seed):
    df = make_clean(seed, rows=random.Random(seed).randint(40, 400))
    issues = find_validity_issues(df)
    assert issues == [], [i["id"] for i in issues]


@pytest.mark.parametrize("seed", SEEDS)
def test_injected_errors_are_found(seed):
    df = make_clean(seed, rows=random.Random(seed).randint(40, 400))
    broken, expected = inject(df, seed)
    found = {(i["category"], i["column"]) for i in find_validity_issues(broken)}
    assert ("duplicate_id", "ref") in found
    assert ("invalid_values", "price") in found       # negative
    assert ("invalid_values", "qty") in found         # zero
    assert ("broken_relationship", "subtotal") in found
    assert ("invalid_values", "email") in found
    assert ("inconsistent_spelling", "city") in found
    assert broken.height and health_report(broken)["score"] < health_report(df)["score"]


def test_relationship_issue_points_at_the_right_row():
    df = make_clean(3, 120)
    broken, _ = inject(df, 3)
    issue = next(i for i in find_validity_issues(broken) if i["category"] == "broken_relationship" and i["column"] == "subtotal")
    bad = broken.with_row_index("r").filter(
        (pl.col("subtotal") - pl.col("qty") * pl.col("price")).abs() > 0.011)["r"].to_list()
    # rows with the zero qty / negative price are still consistent (0 × p is written as q × p), so only the edited row
    assert [r + 1 for r in bad] == issue["evidence"]["rows"][: len(bad)]


def test_anomaly_file_findings():
    df = pl.read_csv(ANOMALY, infer_schema_length=0).with_columns(
        pl.col("units").cast(pl.Int64), pl.col("unit_price").cast(pl.Float64), pl.col("total_amount").cast(pl.Float64))
    found = {i["id"] for i in find_validity_issues(df)}
    assert {"repeated_ids:order_id", "invalid_email:customer_email", "negative:unit_price", "zero:units",
            "relationship:total_amount", "spelling:city"} <= found


def test_small_tables_are_left_alone():
    df = make_clean(1, 10)
    broken, _ = inject(df, 1)
    assert not [i for i in find_validity_issues(broken) if i["category"] == "broken_relationship"]


# ------------------------------------------------------------ safer fills
def test_no_mode_fill_for_unique_values_and_no_unknown_for_records():
    rows = 60
    df = pl.DataFrame({
        "when": [None if i % 9 == 0 else (date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(rows)],
        "mail": [None if i % 8 == 0 else f"u{i}@x.com" for i in range(rows)],
        "colour": [None if i % 3 == 0 else ["red", "blue"][i % 2] for i in range(rows)],
    })
    by_col = {i["column"]: i for i in find_issues(df) if i["category"] == "missing_values"}
    for col in ("when", "mail"):
        ops = [o["operation"] for o in by_col[col]["options"]]
        assert not any(op["type"] == "fill_missing" and op["params"]["strategy"] in ("mode", "constant") for op in ops), col
    with pytest.raises(Exception, match="different"):
        run_operation(df, "fill_missing", {"columns": ["when"], "strategy": "mode"})
    assert any(o["operation"]["params"].get("strategy") == "mode" for o in by_col["colour"]["options"])


def test_preview_warns_when_a_step_breaks_a_total():
    df = make_clean(5, 200).with_columns(pl.when(pl.int_range(pl.len()) == 7).then(99999.0).otherwise(pl.col("price")).alias("price"))
    df = df.with_columns((pl.col("qty") * pl.col("price")).round(2).alias("subtotal"))
    linked = run_operation(df, "handle_outliers", {"column": "price", "method": "iqr", "action": "cap"})
    assert relationship_damage(df, linked.df, {"price"}) == []  # subtotal recalculated in the capped rows
    alone = run_operation(df, "handle_outliers", {"column": "price", "method": "iqr", "action": "cap", "update_linked": False})
    warnings = relationship_damage(df, alone.df, {"price"})
    assert warnings and "subtotal" in warnings[0]
