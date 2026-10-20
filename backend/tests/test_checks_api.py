"""Saved checks: stored per dataset, re-evaluated on every version, counted in Health."""

from pathlib import Path

import pytest

from tests.conftest import USER_A, USER_B

ANOMALY = (Path(__file__).parent / "data" / "anomaly.csv").read_bytes()
AGE_CHECK = {"id": "age-range", "name": "Age between 0 and 120",
             "conditions": [{"column": "age", "op": "not_between", "value": 0, "value2": 120}], "combine": "and"}
PRICE_CHECK = {"id": "price", "name": "Price is never negative",
               "conditions": [{"column": "unit_price", "op": "lt", "value": 0}], "combine": "and"}
TOTAL_CHECK = {"id": "total", "name": "Total = units × price", "conditions": [{
    "column": "total_amount", "op": "differs_from", "tolerance": 0.01,
    "formula": [{"column": "units"}, "*", {"column": "unit_price"}]}], "combine": "and"}


@pytest.fixture
def ds(api, auth_header):
    r = api.post("/v1/datasets", params={"filename": "anomaly.csv"}, content=ANOMALY,
                 headers={**auth_header(sub=USER_A), "Content-Type": "application/octet-stream"})
    assert r.status_code == 201, r.text
    return r.json()


def url(ds, path):
    return f"/v1/datasets/{ds['id']}{path}"


def test_save_list_and_results(api, auth_header, ds):
    h = auth_header(sub=USER_A)
    assert api.get(url(ds, "/checks"), headers=h).json() == {"version": 1, "checks": []}
    r = api.put(url(ds, "/checks"), json={"checks": [AGE_CHECK, TOTAL_CHECK, PRICE_CHECK]}, headers=h)
    assert r.status_code == 200, r.text
    by_id = {c["id"]: c["result"] for c in r.json()["checks"]}
    # 'age' holds the text "unknown", so it is a text column: the check explains instead of guessing
    assert by_id["age-range"]["status"] == "error" and "Change column type" in by_id["age-range"]["error"]
    assert by_id["total"]["status"] == "failing" and by_id["total"]["violations"] == 5
    assert [x["row"] for x in by_id["total"]["rows"]] == [32, 38, 66, 71, 76]
    assert by_id["total"]["columns"] == ["total_amount", "units", "unit_price"]
    assert by_id["price"]["status"] == "failing" and by_id["price"]["rows"][0]["row"] == 38

    # after fixing the text value the same saved check runs on the new version
    api.post(url(ds, "/apply"), json={"type": "mark_missing", "params": {"columns": ["age"], "values": ["unknown"]},
                                     "expected_version": 1}, headers=h)
    conv = api.post(url(ds, "/apply"), json={"type": "convert_type", "params": {"column": "age", "to": "float"},
                                            "expected_version": 2}, headers=h)
    assert conv.status_code == 200, conv.text  # a type-only change is a real change
    age = next(c for c in api.get(url(ds, "/checks"), headers=h).json()["checks"] if c["id"] == "age-range")
    assert age["result"]["status"] == "failing" and {x["row"] for x in age["result"]["rows"]} >= {5, 12}


def test_failing_checks_lower_health_and_show_in_recommendations(api, auth_header, ds):
    h = auth_header(sub=USER_A)
    before = api.get(url(ds, "/health"), headers=h).json()
    api.put(url(ds, "/checks"), json={"checks": [PRICE_CHECK]}, headers=h)
    after = api.get(url(ds, "/health"), headers=h).json()
    assert after["score"] < before["score"]
    assert any(d["reason"] == "Your checks failing" for d in after["deductions"])
    recs = api.get(url(ds, "/recommendations"), headers=h).json()["issues"]
    issue = next(i for i in recs if i["id"] == "check:price")
    assert issue["category"] == "check_failed" and not any(o["recommended"] for o in issue["options"])

    # applying the suggested fix makes the check pass on the next version
    option = next(o for o in issue["options"] if o["operation"]["params"]["action"] == "set_missing")
    applied = api.post(url(ds, "/apply"), json={**option["operation"], "expected_version": 1}, headers=h)
    assert applied.status_code == 200, applied.text
    listed = api.get(url(ds, "/checks"), headers=h).json()
    assert listed["version"] == 2 and listed["checks"][0]["result"]["status"] == "passing"
    assert not any(i["id"] == "check:price" for i in api.get(url(ds, "/health"), headers=h).json()["issues"])


def test_check_on_missing_column_reports_error_not_crash(api, auth_header, ds):
    h = auth_header(sub=USER_A)
    bad = {**AGE_CHECK, "id": "gone", "conditions": [{"column": "nope", "op": "lt", "value": 0}]}
    r = api.put(url(ds, "/checks"), json={"checks": [bad]}, headers=h)
    assert r.status_code == 200 and r.json()["checks"][0]["result"]["status"] == "error"
    health = api.get(url(ds, "/health"), headers=h)
    assert health.status_code == 200 and any(i["category"] == "check_error" for i in health.json()["issues"])


@pytest.mark.parametrize("body", [
    {"checks": [AGE_CHECK, AGE_CHECK]},                                  # duplicate ids
    {"checks": [{**AGE_CHECK, "id": "has space"}]},
    {"checks": [{**AGE_CHECK, "conditions": []}]},
    {"checks": [{**AGE_CHECK, "id": f"c{i}"} for i in range(26)]},       # over the limit
    {"checks": [{**AGE_CHECK, "conditions": [{"column": "age", "op": "explode"}]}]},
])
def test_invalid_payloads_rejected(api, auth_header, ds, body):
    assert api.put(url(ds, "/checks"), json=body, headers=auth_header(sub=USER_A)).status_code == 422


def test_checks_are_private(api, auth_header, ds):
    api.put(url(ds, "/checks"), json={"checks": [AGE_CHECK]}, headers=auth_header(sub=USER_A))
    assert api.get(url(ds, "/checks"), headers=auth_header(sub=USER_B)).status_code == 404
    assert api.put(url(ds, "/checks"), json={"checks": []}, headers=auth_header(sub=USER_B)).status_code == 404


def test_report_counts_checks(api, auth_header, ds):
    h = auth_header(sub=USER_A)
    api.put(url(ds, "/checks"), json={"checks": [AGE_CHECK]}, headers=h)
    html = api.get(url(ds, "/report"), headers=h).text
    assert "Age between 0 and 120" in html
