"""The real Supabase Storage client, against a mocked HTTP transport."""

import json

import httpx
import pytest

from app.storage.client import StorageError, SupabaseStorage


def make(handler, key="sb_secret_abc123"):
    return SupabaseStorage("https://proj.supabase.co", key, "datasets", transport=httpx.MockTransport(handler))


def test_new_secret_key_goes_only_in_apikey_header(tmp_path):
    seen = {}

    def handler(request):
        seen.update(request.headers)
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"Key": "ok"})

    file = tmp_path / "f.parquet"
    file.write_bytes(b"data")
    make(handler).upload("u/d/v1.parquet", file, "application/vnd.apache.parquet")
    assert seen["apikey"] == "sb_secret_abc123"
    assert "authorization" not in seen
    assert seen["x-upsert"] == "false"
    assert seen["url"] == "https://proj.supabase.co/storage/v1/object/datasets/u/d/v1.parquet"


def test_legacy_jwt_key_also_sent_as_bearer(tmp_path):
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200)

    file = tmp_path / "f"
    file.write_bytes(b"x")
    make(handler, key="eyJhbGciOi.legacy.key").upload("p", file, "text/csv")
    assert seen["authorization"] == "Bearer eyJhbGciOi.legacy.key"


def test_download_streams_to_file(tmp_path):
    def handler(request):
        assert request.url.path == "/storage/v1/object/authenticated/datasets/u/d/source.csv"
        return httpx.Response(200, content=b"a,b\n1,2\n")

    target = tmp_path / "out.csv"
    make(handler).download("u/d/source.csv", target)
    assert target.read_bytes() == b"a,b\n1,2\n"


def test_delete_sends_prefixes_in_batches():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=[])

    make(handler).delete([f"p{i}" for i in range(250)])
    assert [len(b["prefixes"]) for b in bodies] == [100, 100, 50]


def test_server_error_retried_once_then_raises(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503, text="busy")

    file = tmp_path / "f"
    file.write_bytes(b"x")
    with pytest.raises(StorageError):
        make(handler).upload("p", file, "text/csv")
    assert len(calls) == 2


def test_transient_error_then_success(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200)

    file = tmp_path / "f"
    file.write_bytes(b"x")
    make(handler).upload("p", file, "text/csv")
    assert len(calls) == 2


def test_conflict_on_retry_counts_as_success(tmp_path):
    responses = iter([httpx.Response(502), httpx.Response(409, json={"error": "Duplicate"})])
    file = tmp_path / "f"
    file.write_bytes(b"x")
    make(lambda r: next(responses)).upload("p", file, "text/csv")


def test_client_error_raises_without_leaking_key(tmp_path, caplog):
    file = tmp_path / "f"
    file.write_bytes(b"x")
    with pytest.raises(StorageError):
        make(lambda r: httpx.Response(400, json={"error": "Invalid"})).upload("p", file, "text/csv")
    assert "sb_secret_abc123" not in caplog.text
