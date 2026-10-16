"""End-to-end API tests for datasets, backed by the in-memory fakes."""

import pytest

from tests.conftest import USER_A, USER_B
from tests.helpers import MESSY_CSV, make_xlsx


def upload(api, auth_header, data: bytes, filename="customers.csv", user=USER_A, **params):
    return api.post(
        "/v1/datasets",
        params={"filename": filename, **params},
        content=data,
        headers={**auth_header(sub=user), "Content-Type": "application/octet-stream"},
    )


def code(response):
    return response.json()["error"]["code"]


# ------------------------------------------------------------------- upload
def test_upload_csv_returns_report_columns_and_suggestions(api, auth_header, fake_storage):
    response = upload(api, auth_header, MESSY_CSV)
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["name"] == "customers"
    assert body["row_count"] == 7 and body["column_count"] == 6
    assert body["current_version"] == 1
    assert body["last_operation"] == {"type": "upload"}

    dtypes = {c["name"]: c["dtype"] for c in body["columns"]}
    assert dtypes == {
        "Customer ID": "integer", "Zip Code": "text", "Age": "text",
        "Signup Date": "date", "Salary": "text", "City": "text",
    }
    assert {s["id"] for s in body["suggestions"]} >= {"convert_to_number:Age", "convert_to_number:Salary"}
    assert body["ingest_report"]["source"]["format"] == "csv"
    assert any("leading zeros" in w for w in body["ingest_report"]["warnings"])

    # source file + v1 parquet stored under the user's folder
    keys = fake_storage.keys()
    assert len(keys) == 2
    assert all(k.startswith(f"{USER_A}/{body['id']}/") for k in keys)
    assert any(k.endswith("/source.csv") for k in keys)
    assert any(k.endswith(".parquet") for k in keys)


def test_parquet_preserves_types_exactly(api, auth_header, fake_storage):
    """The v1 bug: types changed after a restart because the working copy was CSV."""
    import polars as pl

    upload(api, auth_header, MESSY_CSV)
    parquet = next(p for p in fake_storage.root.rglob("*.parquet"))
    df = pl.read_parquet(parquet)
    assert df["Zip Code"].to_list()[0] == "00123"
    assert df["Signup Date"].dtype == pl.Date
    assert df["Customer ID"].dtype == pl.Int64


def test_upload_xlsx_with_sheet_choice(api, auth_header):
    data = make_xlsx({"Summary": [["note"], ["hello"]], "Data": [["id", "amount"], [1, 9.5], [2, 3.0]]})
    response = upload(api, auth_header, data, filename="report.xlsx", sheet="Data")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["sheet_name"] == "Data"
    assert body["sheet_names"] == ["Summary", "Data"]
    assert {c["name"]: c["dtype"] for c in body["columns"]} == {"id": "integer", "amount": "float"}


def test_unsupported_type_rejected_before_reading(api, auth_header):
    response = upload(api, auth_header, b"whatever", filename="old.xls")
    assert response.status_code == 415
    assert code(response) == "unsupported_file_type"


def test_sheet_param_on_csv_rejected(api, auth_header):
    response = upload(api, auth_header, MESSY_CSV, sheet="x")
    assert response.status_code == 422


def test_empty_body_rejected(api, auth_header):
    response = upload(api, auth_header, b"")
    assert response.status_code == 400
    assert code(response) == "empty_upload"


def test_oversized_upload_rejected_by_content_length(api, auth_header, settings):
    big = b"a,b\n" + b"1,2\n" * (settings.max_upload_bytes // 4 + 10)
    response = upload(api, auth_header, big)
    assert response.status_code == 413
    assert code(response) == "file_too_large"


def test_oversized_upload_rejected_while_streaming(api, auth_header, settings):
    """No Content-Length (chunked upload): the limit is enforced while reading."""
    chunk = b"1,2\n" * 1024 * 64  # 256 KB

    def body():
        yield b"a,b\n"
        for _ in range(settings.max_upload_bytes // len(chunk) + 2):
            yield chunk

    response = api.post(
        "/v1/datasets",
        params={"filename": "big.csv"},
        content=body(),
        headers={**auth_header(sub=USER_A), "Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 413


def test_xlsx_has_its_own_lower_limit(settings, verifier, dataset_service, auth_header):
    from fastapi.testclient import TestClient

    from app.main import create_app

    small = settings.model_copy(update={"max_xlsx_upload_mb": 1})
    import random

    rng = random.Random(0)
    big_xlsx = make_xlsx({"S": [["n", "text"]] + [[i, f"{rng.random():.12f}-{rng.random():.12f}"] for i in range(40_000)]})
    assert len(big_xlsx) > 1024 * 1024
    with TestClient(create_app(small, verifier, dataset_service), raise_server_exceptions=False) as api:
        response = upload(api, auth_header, big_xlsx, filename="big.xlsx")
        assert response.status_code == 413
        assert response.json()["error"]["details"] == {"limit_mb": 1}
        # the same number of bytes as CSV is fine (25 MB limit)
        assert upload(api, auth_header, MESSY_CSV + b"8,1,1,2024-01-01,1,X\n" * 60_000).status_code == 201


def test_unreadable_upload_leaves_nothing_behind(api, auth_header, fake_storage, fake_repo):
    response = upload(api, auth_header, b"a,b\n", filename="header_only.csv")
    assert response.status_code == 422
    assert fake_storage.keys() == set()
    assert fake_repo.datasets == {}


def test_storage_failure_returns_503_and_no_db_row(api, auth_header, fake_storage, fake_repo):
    fake_storage.fail_uploads = True
    response = upload(api, auth_header, MESSY_CSV)
    assert response.status_code == 503
    assert code(response) == "storage_unavailable"
    assert fake_repo.datasets == {}


def test_db_failure_cleans_up_uploaded_files(api, auth_header, fake_storage, fake_repo):
    fake_repo.fail_create = True
    response = upload(api, auth_header, MESSY_CSV)
    assert response.status_code == 500
    assert fake_storage.keys() == set()  # both files removed again


def test_quota_enforced(api, auth_header, settings):
    for i in range(settings.max_datasets_per_user):
        assert upload(api, auth_header, MESSY_CSV, filename=f"f{i}.csv").status_code == 201
    response = upload(api, auth_header, MESSY_CSV, filename="one_too_many.csv")
    assert response.status_code == 403
    assert code(response) == "quota_exceeded"


def test_upload_rate_limit(settings, verifier, dataset_service, auth_header):
    from fastapi.testclient import TestClient

    from app.main import create_app

    limited = settings.model_copy(update={"uploads_per_hour": 2})
    with TestClient(create_app(limited, verifier, dataset_service), raise_server_exceptions=False) as api:
        assert upload(api, auth_header, MESSY_CSV, filename="1.csv").status_code == 201
        assert upload(api, auth_header, MESSY_CSV, filename="2.csv").status_code == 201
        response = upload(api, auth_header, MESSY_CSV, filename="3.csv")
    assert response.status_code == 429
    assert response.json()["error"]["details"]["retry_after_seconds"] > 0


def test_upload_requires_auth(api):
    response = api.post("/v1/datasets", params={"filename": "a.csv"}, content=MESSY_CSV)
    assert response.status_code == 401


# --------------------------------------------------------- list / get / rename
def test_list_get_rename(api, auth_header):
    created = upload(api, auth_header, MESSY_CSV).json()
    listing = api.get("/v1/datasets", headers=auth_header(sub=USER_A)).json()
    assert [d["id"] for d in listing["datasets"]] == [created["id"]]
    assert listing["limit"] == 10

    got = api.get(f"/v1/datasets/{created['id']}", headers=auth_header(sub=USER_A))
    assert got.status_code == 200 and got.json()["columns"] == created["columns"]

    renamed = api.patch(f"/v1/datasets/{created['id']}", json={"name": "  Q3 customers  "}, headers=auth_header(sub=USER_A))
    assert renamed.json()["name"] == "Q3 customers"


def test_rename_rejects_blank(api, auth_header):
    created = upload(api, auth_header, MESSY_CSV).json()
    response = api.patch(f"/v1/datasets/{created['id']}", json={"name": "   "}, headers=auth_header(sub=USER_A))
    assert response.status_code == 422


def test_malformed_id_is_422(api, auth_header):
    assert api.get("/v1/datasets/not-a-uuid", headers=auth_header(sub=USER_A)).status_code == 422


# ------------------------------------------------------------------ ownership
@pytest.mark.parametrize(
    "method,path_suffix,json_body",
    [
        ("get", "", None),
        ("patch", "", {"name": "stolen"}),
        ("delete", "", None),
        ("get", "/sheets", None),
        ("post", "/sheet", {"sheet_name": "x", "expected_version": 1}),
    ],
)
def test_other_users_cannot_touch_my_dataset(api, auth_header, method, path_suffix, json_body):
    created = upload(api, auth_header, MESSY_CSV, user=USER_A).json()
    response = api.request(
        method, f"/v1/datasets/{created['id']}{path_suffix}", json=json_body, headers=auth_header(sub=USER_B)
    )
    assert response.status_code == 404
    assert code(response) == "dataset_not_found"
    # and it still exists for the owner
    assert api.get(f"/v1/datasets/{created['id']}", headers=auth_header(sub=USER_A)).status_code == 200


def test_list_only_shows_my_datasets(api, auth_header):
    upload(api, auth_header, MESSY_CSV, user=USER_A)
    assert api.get("/v1/datasets", headers=auth_header(sub=USER_B)).json()["datasets"] == []


# --------------------------------------------------------------------- delete
def test_delete_removes_rows_and_files(api, auth_header, fake_storage, fake_repo):
    created = upload(api, auth_header, MESSY_CSV).json()
    response = api.delete(f"/v1/datasets/{created['id']}", headers=auth_header(sub=USER_A))
    assert response.status_code == 204
    assert fake_storage.keys() == set()
    assert fake_repo.datasets == {} and fake_repo.versions == {}
    assert api.get(f"/v1/datasets/{created['id']}", headers=auth_header(sub=USER_A)).status_code == 404


def test_delete_frees_quota(api, auth_header, settings):
    ids = [upload(api, auth_header, MESSY_CSV, filename=f"{i}.csv").json()["id"] for i in range(settings.max_datasets_per_user)]
    api.delete(f"/v1/datasets/{ids[0]}", headers=auth_header(sub=USER_A))
    assert upload(api, auth_header, MESSY_CSV).status_code == 201


# --------------------------------------------------------------------- sheets
def test_switch_sheet_creates_new_version(api, auth_header, fake_storage):
    data = make_xlsx({"Sales": [["id", "amount"], [1, 2.5]], "Costs": [["cost", "dept"], [3, "IT"], [4, "HR"]]})
    created = upload(api, auth_header, data, filename="book.xlsx").json()
    headers = auth_header(sub=USER_A)

    sheets = api.get(f"/v1/datasets/{created['id']}/sheets", headers=headers).json()
    assert sheets == {"sheet_names": ["Sales", "Costs"], "current": "Sales"}

    switched = api.post(
        f"/v1/datasets/{created['id']}/sheet", json={"sheet_name": "Costs", "expected_version": 1}, headers=headers
    )
    assert switched.status_code == 200, switched.text
    body = switched.json()
    assert body["current_version"] == 2
    assert body["sheet_name"] == "Costs"
    assert body["row_count"] == 2
    assert [c["name"] for c in body["columns"]] == ["cost", "dept"]
    assert body["last_operation"] == {"type": "select_sheet", "params": {"sheet": "Costs"}}
    assert len([k for k in fake_storage.keys() if k.endswith(".parquet")]) == 2  # v1 kept for undo


def test_switch_sheet_with_stale_version_is_409(api, auth_header):
    data = make_xlsx({"A": [["x"], [1]], "B": [["y"], [2]]})
    created = upload(api, auth_header, data, filename="book.xlsx").json()
    headers = auth_header(sub=USER_A)
    api.post(f"/v1/datasets/{created['id']}/sheet", json={"sheet_name": "B", "expected_version": 1}, headers=headers)

    stale = api.post(f"/v1/datasets/{created['id']}/sheet", json={"sheet_name": "A", "expected_version": 1}, headers=headers)
    assert stale.status_code == 409
    assert code(stale) == "version_conflict"
    assert stale.json()["error"]["details"] == {"current_version": 2}


def test_switch_to_unknown_sheet_is_422(api, auth_header):
    created = upload(api, auth_header, make_xlsx({"A": [["x"], [1]]}), filename="book.xlsx").json()
    response = api.post(
        f"/v1/datasets/{created['id']}/sheet", json={"sheet_name": "Nope", "expected_version": 1}, headers=auth_header(sub=USER_A)
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"] == {"sheet_names": ["A"]}


def test_old_versions_pruned_and_files_deleted(settings, verifier, fake_repo, fake_storage, auth_header):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.services.datasets import DatasetLimits, DatasetService

    service = DatasetService(fake_repo, fake_storage, DatasetLimits(max_datasets=10, keep_versions=2))
    with TestClient(create_app(settings, verifier, service), raise_server_exceptions=False) as api:
        created = upload(api, auth_header, make_xlsx({"A": [["x"], [1]], "B": [["y"], [2]]}), filename="b.xlsx").json()
        headers = auth_header(sub=USER_A)
        for version, sheet in [(1, "B"), (2, "A"), (3, "B")]:
            r = api.post(f"/v1/datasets/{created['id']}/sheet", json={"sheet_name": sheet, "expected_version": version}, headers=headers)
            assert r.status_code == 200, r.text

    remaining_versions = sorted(v for (d, v) in fake_repo.versions if str(d) == created["id"])
    assert remaining_versions == [3, 4]
    assert len([k for k in fake_storage.keys() if k.endswith(".parquet")]) == 2


# ------------------------------------------------------------------------ /me
def test_me_reports_usage(api, auth_header):
    upload(api, auth_header, MESSY_CSV)
    usage = api.get("/v1/me", headers=auth_header(sub=USER_A)).json()["usage"]
    assert usage["datasets"] == 1 and usage["max_datasets"] == 10
    assert usage["storage_bytes"] > len(MESSY_CSV)


def test_dataset_endpoints_503_when_not_configured(client, auth_header):
    response = client.get("/v1/datasets", headers=auth_header())
    assert response.status_code == 503
    assert code(response) == "not_configured"
