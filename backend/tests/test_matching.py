"""'Show rows': every issue that points at rows must find exactly those rows."""

from pathlib import Path

import polars as pl
import pytest

from app.engine.matching import matching_rows
from app.engine.quality import find_issues
from app.engine.rules import Condition
from app.services.checks import CheckDefinition, check_issues, evaluate_checks
from tests.conftest import USER_A
from tests.test_validity import SEEDS, inject, make_clean

ANOMALY = Path(__file__).parent / "data" / "anomaly.csv"
COUNT_KEYS = {"missing_values": "missing", "outliers": "count", "invalid_values": None, "broken_relationship": "violations",
              "duplicate_id": "rows_involved"}


def run(df, match, offset=0, limit=100):
    conds = [Condition.model_validate(c) for c in match.get("conditions", [])]
    return matching_rows(df, conds, match.get("combine", "and"), bool(match.get("duplicate_rows")), offset, limit)


def assert_matches_agree(df):
    for issue in find_issues(df):
        match = issue.get("match")
        if issue["category"] in ("empty_column", "constant_column"):
            assert match is None
            continue
        if match is None:
            continue
        result = run(df, match)
        if issue["category"] != "type_mismatch":  # a text column of valid dates is a type issue with no bad rows
            assert result["total"] > 0, issue["id"]
        key = COUNT_KEYS.get(issue["category"])
        if key and key in issue["evidence"]:
            assert result["total"] == issue["evidence"][key], issue["id"]
        if issue["category"] == "duplicates":
            assert result["total"] >= 2 * 1
        if "rows" in issue["evidence"] and isinstance(issue["evidence"]["rows"], list):
            shown = [r["row"] for r in result["rows"]]
            assert issue["evidence"]["rows"] == shown[: len(issue["evidence"]["rows"])], issue["id"]


def test_anomaly_every_issue_shows_its_rows():
    df = pl.read_csv(ANOMALY, infer_schema_length=0).with_columns(
        pl.col("units").cast(pl.Int64), pl.col("unit_price").cast(pl.Float64), pl.col("total_amount").cast(pl.Float64))
    assert_matches_agree(df)


@pytest.mark.parametrize("seed", SEEDS[:15])
def test_random_datasets_every_issue_shows_its_rows(seed):
    broken, _ = inject(make_clean(seed, 150), seed)
    assert_matches_agree(broken)


def test_duplicate_rows_are_shown_next_to_each_other():
    df = pl.DataFrame({"a": [1, 2, 1, 3, 2], "b": ["x", "y", "x", "z", "y"]})
    result = run(df, {"duplicate_rows": True})
    assert result["total"] == 4 and [r["row"] for r in result["rows"]] == [1, 3, 2, 5]


def test_paging_and_highlight():
    df = pl.DataFrame({"q": list(range(50)), "p": [1.0] * 50, "t": [float(i) for i in range(50)]})
    match = {"conditions": [{"column": "t", "op": "differs_from", "formula": [{"column": "q"}, "*", {"column": "p"}]},
                            {"column": "q", "op": "ge", "value": 0}], "combine": "or"}
    page = run(df, match, offset=40, limit=5)
    assert page["total"] == 50 and [r["row"] for r in page["rows"]] == [41, 42, 43, 44, 45]
    assert page["highlight"] == ["t", "q", "p"]


def test_check_issue_match_uses_check_conditions():
    df = pl.DataFrame({"age": [10, -1, 200, 30] * 6})
    check = CheckDefinition.model_validate({"id": "a", "name": "Age ok", "combine": "or", "conditions": [
        {"column": "age", "op": "lt", "value": 0}, {"column": "age", "op": "gt", "value": 120}]})
    issue = check_issues([check], evaluate_checks(df, [check]))[0]
    assert run(df, issue["match"])["total"] == 12


def test_extra_spaces_operator():
    df = pl.DataFrame({"c": ["a", " a", "b ", None, "c"]})
    assert [r["row"] for r in run(df, {"conditions": [{"column": "c", "op": "extra_spaces"}]})["rows"]] == [2, 3]


def test_matching_rows_endpoint(api, auth_header):
    h = {**auth_header(sub=USER_A)}
    ds = api.post("/v1/datasets", params={"filename": "anomaly.csv"}, content=ANOMALY.read_bytes(),
                  headers={**h, "Content-Type": "application/octet-stream"}).json()
    issues = api.get(f"/v1/datasets/{ds['id']}/health", headers=h).json()["issues"]
    rel = next(i for i in issues if i["category"] == "broken_relationship")
    r = api.post(f"/v1/datasets/{ds['id']}/matching-rows", json={**rel["match"], "limit": 3}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 1 and body["total"] == 5 and len(body["rows"]) == 3
    assert body["highlight"] == ["total_amount", "units", "unit_price"] and len(body["rows"][0]["values"]) == len(body["columns"])
    bad = api.post(f"/v1/datasets/{ds['id']}/matching-rows", json={"conditions": [{"column": "nope", "op": "is_empty"}]}, headers=h)
    assert bad.status_code == 422
    assert api.post(f"/v1/datasets/{ds['id']}/matching-rows", json={"conditions": []}, headers=h).status_code == 422
