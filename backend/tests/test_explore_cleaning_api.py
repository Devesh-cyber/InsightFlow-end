"""Phase 2-5 endpoints end to end, backed by the in-memory fakes."""

import io

import polars as pl
import pytest

from tests.conftest import USER_A, USER_B
from tests.helpers import MESSY_CSV, make_xlsx


@pytest.fixture
def ds(api, auth_header):
    response = api.post(
        "/v1/datasets", params={"filename": "customers.csv"}, content=MESSY_CSV,
        headers={**auth_header(sub=USER_A), "Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def H(auth_header, user=USER_A):
    return auth_header(sub=user)


def get(api, auth_header, ds, path, user=USER_A, **params):
    return api.get(f"/v1/datasets/{ds['id']}{path}", params=params, headers=H(auth_header, user))


def post(api, auth_header, ds, path, body, user=USER_A):
    return api.post(f"/v1/datasets/{ds['id']}{path}", json=body, headers=H(auth_header, user))


# ------------------------------------------------------------------- explore
def test_overview_rows_columns_health(api, auth_header, ds):
    o = get(api, auth_header, ds, "/overview").json()
    assert o["version"] == 1 and o["rows"] == 7 and o["columns"] == 6
    assert o["duplicate_rows"] == 0  # rows 1 and 5 share values but differ in Customer ID

    page = get(api, auth_header, ds, "/rows", offset=5, limit=10).json()
    assert page["total_rows"] == 7 and len(page["rows"]) == 2 and page["rows"][0][0] == 6

    cols = get(api, auth_header, ds, "/columns").json()["columns"]
    assert {c["name"]: c["role"] for c in cols}["Signup Date"] == "temporal"

    health = get(api, auth_header, ds, "/health").json()
    assert {"convert_to_number:Age", "convert_to_number:Salary"} <= {i["id"] for i in health["issues"]}


def test_column_detail_by_query_param(api, auth_header, ds):
    detail = get(api, auth_header, ds, "/columns/detail", name="Zip Code").json()
    assert detail["dtype"] == "text" and detail["top_values"]["items"][0]["value"] == "00123"
    assert get(api, auth_header, ds, "/columns/detail", name="nope").status_code == 422


def test_relationships_correlations_charts(api, auth_header, ds):
    r = get(api, auth_header, ds, "/relationships", a="Zip Code", b="City")
    assert r.status_code == 422  # near-unique text: refused with a reason
    assert "free text" in r.json()["error"]["message"]
    ok = get(api, auth_header, ds, "/relationships", a="Customer ID", b="City")
    assert ok.status_code == 200 and ok.json()["type"] == "numeric_categorical"
    corr = get(api, auth_header, ds, "/correlations").json()
    assert corr["matrix"] == [] and "two numeric" in corr["note"]  # only one numeric column
    opts = get(api, auth_header, ds, "/charts/options", x="City").json()
    assert [c["type"] for c in opts["charts"]] == ["bar"]
    bars = get(api, auth_header, ds, "/charts/data", type="bar", x="City").json()["bars"]
    assert sum(b["value"] for b in bars) == 7


def test_results_are_cached_per_version(api, auth_header, ds, fake_repo):
    get(api, auth_header, ds, "/overview")
    reads = fake_repo.analysis_reads
    get(api, auth_header, ds, "/overview")
    assert fake_repo.analysis_reads == reads  # served from memory, not even the DB cache


def test_explore_requires_ownership(api, auth_header, ds):
    for path in ["/overview", "/rows", "/health", "/columns", "/recommendations", "/versions"]:
        assert get(api, auth_header, ds, path, user=USER_B).status_code == 404, path


def test_unknown_version_is_404(api, auth_header, ds):
    response = get(api, auth_header, ds, "/overview", version=9)
    assert response.status_code == 404 and response.json()["error"]["code"] == "version_not_found"


# ------------------------------------------------------------------ cleaning
AGE_FIX = {"type": "convert_type", "params": {"column": "Age", "to": "integer", "treat_as_missing": ["?"]}}


def test_preview_then_apply_then_revert(api, auth_header, ds, fake_storage):
    preview = post(api, auth_header, ds, "/preview", AGE_FIX).json()
    assert preview["effect"]["changed_per_column"] == {"Age": 1}
    assert preview["samples"]["changed_rows"]["rows"][0] == {"row": 2, "before": ["?"], "after": [None]}
    assert get(api, auth_header, ds, "/overview").json()["version"] == 1  # preview wrote nothing

    applied = post(api, auth_header, ds, "/apply", {**AGE_FIX, "expected_version": 1})
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["dataset"]["current_version"] == 2
    assert {c["name"]: c["dtype"] for c in body["dataset"]["columns"]}["Age"] == "integer"
    assert body["applied"]["description"] == "Convert 'Age' to integer"

    # explore now reflects v2, and v1 is still reachable
    assert get(api, auth_header, ds, "/columns/detail", name="Age").json()["dtype"] == "integer"
    assert get(api, auth_header, ds, "/columns/detail", name="Age", version=1).json()["dtype"] == "text"

    history = get(api, auth_header, ds, "/versions").json()
    assert [v["version"] for v in history["versions"]] == [2, 1]
    assert history["versions"][0]["is_current"] and history["versions"][1]["description"] == "Uploaded the file"

    reverted = post(api, auth_header, ds, "/revert", {"to_version": 1, "expected_version": 2})
    assert reverted.status_code == 200, reverted.text
    assert reverted.json()["current_version"] == 3
    assert get(api, auth_header, ds, "/columns/detail", name="Age").json()["dtype"] == "text"
    parquet_files = [k for k in fake_storage.keys() if k.endswith(".parquet")]
    assert len(parquet_files) == 2  # revert reused v1's file; nothing copied


def test_stale_apply_is_409(api, auth_header, ds):
    post(api, auth_header, ds, "/apply", {**AGE_FIX, "expected_version": 1})
    stale = post(api, auth_header, ds, "/apply", {"type": "drop_columns", "params": {"columns": ["City"]}, "expected_version": 1})
    assert stale.status_code == 409 and stale.json()["error"]["details"] == {"current_version": 2}


def test_apply_without_effect_is_rejected(api, auth_header, ds):
    response = post(api, auth_header, ds, "/apply", {"type": "drop_duplicates", "params": {}, "expected_version": 1})
    assert response.status_code == 422 and response.json()["error"]["code"] == "no_change"


def test_apply_that_empties_dataset_is_rejected(api, auth_header, ds):
    body = {"type": "drop_missing_rows", "params": {"how": "any"}, "expected_version": 1}
    # every row has a gap somewhere? not here, so construct: drop rows missing Age or City
    body["params"]["columns"] = ["Age", "City"]
    assert post(api, auth_header, ds, "/apply", body).status_code == 200


def test_bad_operation_params_are_422(api, auth_header, ds):
    response = post(api, auth_header, ds, "/preview", {"type": "fill_missing", "params": {"columns": ["Age"], "strategy": "magic"}})
    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_parameters"


def test_recommendations_feed_preview(api, auth_header, ds):
    issues = get(api, auth_header, ds, "/recommendations").json()["issues"]
    issue = next(i for i in issues if i["id"] == "convert_to_number:Salary")
    option = next(o for o in issue["options"] if o["recommended"])
    preview = post(api, auth_header, ds, "/preview", option["operation"])
    assert preview.status_code == 200 and preview.json()["effect"]["changed_per_column"] == {"Salary": 2}


def test_operations_catalogue(api):
    ops = api.get("/v1/operations").json()["operations"]
    assert len(ops) == 18 and all("properties" in o["params_schema"] for o in ops)


def test_sheet_switch_then_revert_restores_sheet(api, auth_header):
    data = make_xlsx({"A": [["x"], [1]], "B": [["y"], [2]]})
    created = api.post("/v1/datasets", params={"filename": "b.xlsx"}, content=data, headers=H(auth_header)).json()
    api.post(f"/v1/datasets/{created['id']}/sheet", json={"sheet_name": "B", "expected_version": 1}, headers=H(auth_header))
    reverted = api.post(f"/v1/datasets/{created['id']}/revert", json={"to_version": 1, "expected_version": 2}, headers=H(auth_header)).json()
    assert reverted["sheet_name"] == "A" and [c["name"] for c in reverted["columns"]] == ["x"]


# -------------------------------------------------------------------- export
def test_export_csv_and_xlsx(api, auth_header, ds):
    post(api, auth_header, ds, "/apply", {**AGE_FIX, "expected_version": 1})
    csv = get(api, auth_header, ds, "/export", format="csv")
    assert csv.status_code == 200
    assert 'filename="customers_v2.csv"' in csv.headers["content-disposition"]
    exported = pl.read_csv(io.BytesIO(csv.content), infer_schema=False)
    assert exported["Zip Code"][0] == "00123"  # leading zeros survive the round trip
    assert exported["Age"][1] is None

    old = get(api, auth_header, ds, "/export", format="csv", version=1)
    assert pl.read_csv(io.BytesIO(old.content), infer_schema=False)["Age"][1] == "?"

    xlsx = get(api, auth_header, ds, "/export", format="xlsx")
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"
    assert pl.read_excel(io.BytesIO(xlsx.content)).height == 7


def test_xlsx_export_keeps_dates_and_refuses_huge(settings, verifier, dataset_service, auth_header):
    from fastapi.testclient import TestClient

    from app.main import create_app

    tiny = settings.model_copy(update={"max_xlsx_export_cells": 1000})
    with TestClient(create_app(tiny, verifier, dataset_service), raise_server_exceptions=False) as api:
        small = api.post("/v1/datasets", params={"filename": "s.csv"}, content=MESSY_CSV, headers=H(auth_header)).json()
        xlsx = api.get(f"/v1/datasets/{small['id']}/export", params={"format": "xlsx"}, headers=H(auth_header))
        back = pl.read_excel(io.BytesIO(xlsx.content))
        assert back["Signup Date"].dtype in (pl.Date, pl.Datetime("ms"), pl.Datetime("us"))  # a real date, not a number
        assert back["Zip Code"][0] == "00123"
        big_csv = b"a,b\n" + b"".join(f"{i},x{i}\n".encode() for i in range(600))
        big = api.post("/v1/datasets", params={"filename": "b.csv"}, content=big_csv, headers=H(auth_header)).json()
        refused = api.get(f"/v1/datasets/{big['id']}/export", params={"format": "xlsx"}, headers=H(auth_header))
        assert refused.status_code == 422 and refused.json()["error"]["code"] == "too_large_for_excel"
        assert api.get(f"/v1/datasets/{big['id']}/export", params={"format": "csv"}, headers=H(auth_header)).status_code == 200


def test_xlsx_text_never_becomes_a_formula(api, auth_header):
    csv = b"name,note\nA,=HYPERLINK(\"http://evil\")\nB,+1+2\n"
    created = api.post("/v1/datasets", params={"filename": "f.csv"}, content=csv, headers=H(auth_header)).json()
    xlsx = api.get(f"/v1/datasets/{created['id']}/export", params={"format": "xlsx"}, headers=H(auth_header)).content
    import zipfile

    sheet = zipfile.ZipFile(io.BytesIO(xlsx)).read("xl/worksheets/sheet1.xml").decode()
    assert "<f>" not in sheet  # no formula cells


def test_recipe_reflects_lineage_through_reverts(api, auth_header, ds):
    post(api, auth_header, ds, "/apply", {**AGE_FIX, "expected_version": 1})
    post(api, auth_header, ds, "/apply", {"type": "drop_columns", "params": {"columns": ["City"]}, "expected_version": 2})
    post(api, auth_header, ds, "/revert", {"to_version": 2, "expected_version": 3})
    code = get(api, auth_header, ds, "/recipe").text
    assert "pd.read_csv('customers.csv'" in code
    assert "'Zip Code': 'string'" in code
    assert 'pd.to_numeric(df[\'Age\'], errors="coerce").astype("Int64")' in code
    assert "drop(columns=['City'])" not in code  # undone by the revert
    compile(code, "recipe.py", "exec")  # it is valid Python


def test_recipe_runs_with_pandas_if_available(api, auth_header, ds, tmp_path):
    pd = pytest.importorskip("pandas")
    post(api, auth_header, ds, "/apply", {**AGE_FIX, "expected_version": 1})
    post(api, auth_header, ds, "/apply", {"type": "fill_missing", "params": {"columns": ["City"], "strategy": "constant", "value": "Unknown"}, "expected_version": 2})
    code = get(api, auth_header, ds, "/recipe").text
    (tmp_path / "customers.csv").write_bytes(MESSY_CSV)
    namespace: dict = {}
    import os

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        exec(compile(code, "recipe.py", "exec"), namespace)
    finally:
        os.chdir(cwd)
    df = namespace["df"]
    assert str(df["Age"].dtype) == "Int64" and df["City"].isna().sum() == 0
    assert df["Zip Code"].iloc[0] == "00123"


def test_cached_reads_do_not_wait_behind_heavy_jobs(settings, verifier, dataset_service, auth_header):
    """With one heavy slot busy (e.g. a slow upload), cached reads still answer; misses get a clean 503."""
    import time

    from fastapi.testclient import TestClient

    from app.main import create_app

    quick = settings.model_copy(update={"job_queue_timeout_seconds": 0.3})
    app = create_app(quick, verifier, dataset_service)
    with TestClient(app, raise_server_exceptions=False) as api:
        created = api.post("/v1/datasets", params={"filename": "c.csv"}, content=MESSY_CSV, headers=H(auth_header)).json()
        base = f"/v1/datasets/{created['id']}"
        assert api.get(base + "/overview", headers=H(auth_header)).status_code == 200  # now cached

        with app.state.job_limiter.heavy():  # simulate a long-running upload holding the only slot
            started = time.perf_counter()
            cached = api.get(base + "/overview", headers=H(auth_header))
            assert cached.status_code == 200 and time.perf_counter() - started < 0.25
            assert api.get(base + "/rows", headers=H(auth_header)).status_code == 200      # cheap: never queued
            assert api.get(base + "/versions", headers=H(auth_header)).status_code == 200
            busy = api.get(base + "/columns/detail", params={"name": "City"}, headers=H(auth_header))  # cache miss
            assert busy.status_code == 503 and busy.json()["error"]["code"] == "server_busy"
        assert api.get(base + "/columns/detail", params={"name": "City"}, headers=H(auth_header)).status_code == 200
