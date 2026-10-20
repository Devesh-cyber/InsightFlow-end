"""Primary vs secondary issues, skew notes, calculated columns, and 'Looks fine, keep it'."""

import random
from pathlib import Path

import polars as pl

from app.engine.quality import find_issues, health_report
from tests.conftest import USER_A, USER_B

ANOMALY = (Path(__file__).parent / "data" / "anomaly.csv").read_bytes()


def frame():
    return pl.read_csv(Path(__file__).parent / "data" / "anomaly.csv", infer_schema_length=0).with_columns(
        pl.col("units").cast(pl.Int64), pl.col("unit_price").cast(pl.Float64), pl.col("total_amount").cast(pl.Float64))


def test_every_issue_has_a_stage_and_secondary_never_costs_points():
    report = health_report(frame())
    stages = {i["category"]: i["stage"] for i in report["issues"]}
    assert stages["outliers"] == "secondary"
    assert all(stages[c] == "primary" for c in ("duplicate_id", "invalid_values", "broken_relationship", "missing_values"))
    assert report["stages"]["secondary"]["open"] >= 1 and report["stages"]["primary"]["open"] >= 5
    no_secondary = [i for i in report["issues"] if i["stage"] == "primary"]
    from app.engine.quality import score_issues
    assert score_issues(no_secondary, 120, 10)["score"] == report["score"]


def test_calculated_column_outliers_offer_no_cap():
    issue = next(i for i in find_issues(frame()) if i["id"] == "outliers:total_amount")
    assert issue["options"] == [] and "units × unit_price" in issue["message"]
    price = next(i for i in find_issues(frame()) if i["id"] == "outliers:unit_price")
    cap = next(o for o in price["options"] if o["operation"]["params"]["action"] == "cap")
    assert "Breaks the formula" in cap["note"] and "total_amount" in price["message"]


def test_skew_note_only_for_skewed_columns():
    rnd = random.Random(1)
    df = pl.DataFrame({"amount": [round(rnd.lognormvariate(3, 1), 2) for _ in range(300)],
                       "height": [round(rnd.gauss(170, 8), 1) for _ in range(300)]})
    found = {i["id"]: i for i in find_issues(df)}
    assert found["skewed:amount"]["stage"] == "secondary" and found["skewed:amount"]["options"] == []
    assert "skewed:height" not in found


def _ds(api, auth_header):
    return api.post("/v1/datasets", params={"filename": "anomaly.csv"}, content=ANOMALY,
                    headers={**auth_header(sub=USER_A), "Content-Type": "application/octet-stream"}).json()


def test_looks_fine_closes_an_issue_and_raises_the_score(api, auth_header):
    h = auth_header(sub=USER_A)
    ds = _ds(api, auth_header)
    url = f"/v1/datasets/{ds['id']}"
    before = api.get(url + "/health", headers=h).json()
    issue = next(i for i in before["issues"] if i["category"] == "duplicate_id" and i["column"] == "customer_name")
    assert issue["reviewed"] is False and len(issue["fingerprint"]) == 16
    mark = {"issue_id": issue["id"], "fingerprint": issue["fingerprint"], "title": issue["title"]}
    r = api.put(url + "/reviewed", json={"reviewed": [mark]}, headers=h)
    assert r.status_code == 200 and r.json()["reviewed"][0]["issue_id"] == issue["id"]
    after = api.get(url + "/health", headers=h).json()
    same = next(i for i in after["issues"] if i["id"] == issue["id"])
    assert same["reviewed"] is True
    assert after["score"] > before["score"]
    assert after["stages"]["primary"]["open"] == before["stages"]["primary"]["open"] - 1
    assert after["stages"]["primary"]["reviewed"] == 1
    recs = api.get(url + "/recommendations", headers=h).json()["issues"]
    assert next(i for i in recs if i["id"] == issue["id"])["reviewed"] is True
    assert api.get(url + "/reviewed", headers=h).json()["reviewed"][0]["fingerprint"] == issue["fingerprint"]


def test_review_goes_stale_when_the_issue_changes(api, auth_header):
    h = auth_header(sub=USER_A)
    ds = _ds(api, auth_header)
    url = f"/v1/datasets/{ds['id']}"
    issue = next(i for i in api.get(url + "/health", headers=h).json()["issues"] if i["id"] == "missing_values:city")
    api.put(url + "/reviewed", json={"reviewed": [{"issue_id": "outliers:units",
                                                   "fingerprint": "0" * 16}]}, headers=h)
    units = next(i for i in api.get(url + "/health", headers=h).json()["issues"] if i["id"] == "outliers:units")
    assert units["reviewed"] is False and units["changed_since_review"] is True
    assert issue["stage"] == "primary"


def test_reviewed_validation_and_privacy(api, auth_header):
    ds = _ds(api, auth_header)
    url = f"/v1/datasets/{ds['id']}/reviewed"
    assert api.put(url, json={"reviewed": [{"issue_id": "x", "fingerprint": "nothex"}]}, headers=auth_header(sub=USER_A)).status_code == 422
    assert api.get(url, headers=auth_header(sub=USER_B)).status_code == 404
    assert api.put(url, json={"reviewed": []}, headers=auth_header(sub=USER_B)).status_code == 404


def test_report_lists_reviewed_items(api, auth_header):
    h = auth_header(sub=USER_A)
    ds = _ds(api, auth_header)
    url = f"/v1/datasets/{ds['id']}"
    issue = next(i for i in api.get(url + "/health", headers=h).json()["issues"] if i["id"] == "outliers:units")
    api.put(url + "/reviewed", json={"reviewed": [{"issue_id": issue["id"], "fingerprint": issue["fingerprint"],
                                                   "title": issue["title"]}]}, headers=h)
    html = api.get(url + "/report", headers=h).text
    assert "Reviewed and kept" in html and issue["title"].replace("'", "&#39;") in html


def test_a_single_wrong_value_is_not_called_skew():
    found = {i["id"] for i in find_issues(frame())}
    assert "skewed:units" not in found and "skewed:unit_price" not in found  # 10,000 and 999,999.99 are outliers
    assert "outliers:units" in found and "outliers:unit_price" in found
