"""Stage 3 API: suggestions, saved plan, preview, export (CSV / train-test ZIP)."""

import io
import json
import zipfile
from pathlib import Path

import polars as pl
import pytest

from tests.conftest import USER_A, USER_B

ANOMALY = (Path(__file__).parent / "data" / "anomaly.csv").read_bytes()
PLAN = {
    "columns": {
        "order_id": {"role": "drop"}, "customer_name": {"role": "drop"}, "customer_email": {"role": "drop"},
        "order_date": {"role": "drop"}, "age": {"role": "drop"},
        "city": {"steps": [{"kind": "fill_missing", "strategy": "mode"}, {"kind": "encode", "method": "onehot"}]},
        "product_category": {"role": "target"},
        "unit_price": {"steps": [{"kind": "clip", "method": "percentile", "lower": 1, "upper": 99}, {"kind": "scale", "method": "robust"}]},
        "total_amount": {"steps": [{"kind": "fill_missing", "strategy": "median"}, {"kind": "transform", "method": "log1p"}]},
    },
    "features": [{"name": "price_x2", "formula": [{"column": "unit_price"}, "*", {"number": 2}]}],
    "split": {"enabled": False},
}


def upload(api, auth_header):
    r = api.post("/v1/datasets", params={"filename": "anomaly.csv"}, content=ANOMALY,
                 headers={**auth_header(sub=USER_A), "Content-Type": "application/octet-stream"})
    return r.json()


def keep_everything(api, auth_header, ds):
    """Mark every open issue 'looks fine' so the ML-ready stage unlocks (the data itself is unchanged)."""
    h = auth_header(sub=USER_A)
    issues = api.get(f"/v1/datasets/{ds['id']}/health", headers=h).json()["issues"]
    marks = [{"issue_id": i["id"], "fingerprint": i["fingerprint"], "title": i["title"]} for i in issues]
    assert api.put(f"/v1/datasets/{ds['id']}/reviewed", json={"reviewed": marks}, headers=h).status_code == 200


@pytest.fixture
def raw(api, auth_header):
    return upload(api, auth_header)


@pytest.fixture
def ds(api, auth_header):
    created = upload(api, auth_header)
    keep_everything(api, auth_header, created)
    return created


def url(ds, path=""):
    return f"/v1/datasets/{ds['id']}/ml{path}"


def test_view_has_suggestions_and_empty_plan(api, auth_header, ds):
    body = api.get(url(ds), headers=auth_header(sub=USER_A)).json()
    assert body["plan"]["columns"] == {} and body["version"] == 1
    assert body["readiness"] == {"locked": False, "primary_open": 0, "secondary_open": 0, "version": 1}
    s = body["suggestions"]
    assert s["order_id"]["suggested_role"] == "drop"
    assert s["total_amount"]["suggested_role"] == "drop" and "redundant" in s["total_amount"]["role_reason"]
    assert s["city"]["steps"][-1]["kind"] == "encode"


def test_save_preview_and_export_csv(api, auth_header, ds):
    h = auth_header(sub=USER_A)
    assert api.put(url(ds, "/plan"), json=PLAN, headers=h).status_code == 200
    assert api.get(url(ds), headers=h).json()["plan"]["columns"]["city"]["steps"][1]["method"] == "onehot"
    p = api.post(url(ds, "/preview"), json=PLAN, headers=h)
    assert p.status_code == 200, p.text
    prev = p.json()
    cols = [c["name"] for c in prev["columns"]]
    assert cols[-1] == "product_category" and "city_Mumbai" in cols and "order_id" not in cols and "price_x2" in cols
    price = next(c for c in prev["columns"] if c["name"] == "unit_price")
    assert price["before"]["max"] == 999999.99 and price["after"]["max"] < 10
    assert prev["shape_after"][0] == 119  # 1 row has no product_category (the target) and is left out
    assert any("empty target" in w for w in prev["warnings"])
    r = api.get(url(ds, "/export"), headers=h)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"].endswith('_v1_ml_ready.csv"')
    out = pl.read_csv(io.BytesIO(r.content))
    assert out.columns == cols and out.height == 119


def test_export_zip_with_train_test_and_plan_json(api, auth_header, ds):
    h = auth_header(sub=USER_A)
    plan = {**PLAN, "split": {"enabled": True, "test_size": 0.25, "seed": 1}}
    api.put(url(ds, "/plan"), json=plan, headers=h)
    r = api.get(url(ds, "/export"), headers=h)
    assert r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(z.namelist()) == ["README.txt", "plan.json", "test.csv", "train.csv"]
    train, test = pl.read_csv(z.open("train.csv")), pl.read_csv(z.open("test.csv"))
    assert train.height + test.height == 119 and 25 <= test.height <= 35
    meta = json.loads(z.read("plan.json"))
    assert meta["split"]["seed"] == 1 and "unit_price" in meta["learned_from_train_rows"]
    assert "TRAIN rows only" in z.read("README.txt").decode()


@pytest.mark.parametrize("bad", [
    {"columns": {"a": {"role": "target"}, "b": {"role": "target"}}},
    {"columns": {"a": {"steps": [{"kind": "explode"}]}}},
    {"columns": {"a": {"steps": [{"kind": "clip", "method": "percentile", "lower": 90, "upper": 10}]}}},
    {"split": {"enabled": True, "test_size": 0.9}},
])
def test_invalid_plans_are_rejected(api, auth_header, ds, bad):
    assert api.put(url(ds, "/plan"), json=bad, headers=auth_header(sub=USER_A)).status_code == 422


def test_plan_errors_explain_the_column(api, auth_header, ds):
    bad = {"columns": {"city": {"steps": [{"kind": "scale"}]}}}
    r = api.post(url(ds, "/preview"), json=bad, headers=auth_header(sub=USER_A))
    assert r.status_code == 422 and "'city'" in r.json()["error"]["message"]


def test_ml_is_private(api, auth_header, ds):
    assert api.get(url(ds), headers=auth_header(sub=USER_B)).status_code == 404
    assert api.put(url(ds, "/plan"), json={}, headers=auth_header(sub=USER_B)).status_code == 404
    assert api.get(url(ds, "/export"), headers=auth_header(sub=USER_B)).status_code == 404


def test_locked_until_cleaning_is_done(api, auth_header, raw):
    h = auth_header(sub=USER_A)
    view = api.get(url(raw), headers=h)
    assert view.status_code == 200  # the page can always be viewed
    ready = view.json()["readiness"]
    assert ready["locked"] is True and ready["primary_open"] > 0 and ready["secondary_open"] > 0
    for call in (lambda: api.put(url(raw, "/plan"), json=PLAN, headers=h),
                 lambda: api.post(url(raw, "/preview"), json=PLAN, headers=h),
                 lambda: api.get(url(raw, "/export"), headers=h)):
        r = call()
        assert r.status_code == 409 and r.json()["error"]["code"] == "clean_first"
        assert "Finish cleaning first" in r.json()["error"]["message"]
    keep_everything(api, auth_header, raw)
    assert api.get(url(raw), headers=h).json()["readiness"]["locked"] is False
    assert api.post(url(raw, "/preview"), json=PLAN, headers=h).status_code == 200


def test_new_issues_lock_it_again_and_keep_the_plan(api, auth_header, ds):
    h = auth_header(sub=USER_A)
    assert api.put(url(ds, "/plan"), json=PLAN, headers=h).status_code == 200
    step = {"type": "apply_rule", "params": {"conditions": [{"column": "units", "op": "gt", "value": 5}],
                                             "action": "set_missing", "target": "units"}, "expected_version": 1}
    assert api.post(f"/v1/datasets/{ds['id']}/apply", json=step, headers=h).status_code == 200
    view = api.get(url(ds), headers=h).json()
    assert view["readiness"]["locked"] is True and view["readiness"]["version"] == 2
    assert view["plan"]["columns"]["city"]["steps"][1]["method"] == "onehot"  # nothing lost
    assert api.get(url(ds, "/export"), headers=h).status_code == 409
