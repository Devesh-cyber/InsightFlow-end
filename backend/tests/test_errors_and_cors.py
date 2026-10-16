from fastapi import Query

from app.core.errors import NotFoundError


def _shape_ok(body: dict) -> bool:
    error = body.get("error", {})
    return set(error) == {"code", "message", "details", "request_id"}


def test_unknown_route_uses_standard_error_shape(client):
    response = client.get("/v1/does-not-exist")
    assert response.status_code == 404
    assert _shape_ok(response.json())
    assert response.json()["error"]["code"] == "not_found"


def test_method_not_allowed_shape(client):
    response = client.delete("/health")
    assert response.status_code == 405
    assert _shape_ok(response.json())


def test_validation_error_shape(client):
    app = client.app

    @app.get("/_test/validate")
    def _validate(n: int = Query(..., ge=1)):
        return {"n": n}

    response = client.get("/_test/validate", params={"n": "zero"})
    assert response.status_code == 422
    body = response.json()
    assert _shape_ok(body)
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"]["fields"][0]["field"] == "query.n"


def test_app_errors_keep_their_code(client):
    app = client.app

    @app.get("/_test/missing")
    def _missing():
        raise NotFoundError("Dataset not found.", code="dataset_not_found")

    response = client.get("/_test/missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "dataset_not_found"


def test_crash_returns_500_without_leaking_internals(client):
    app = client.app

    @app.get("/_test/crash")
    def _crash():
        raise RuntimeError("database password is hunter2")

    response = client.get("/_test/crash")
    assert response.status_code == 500
    body = response.json()
    assert _shape_ok(body)
    assert "hunter2" not in response.text
    assert body["error"]["request_id"] == response.headers["x-request-id"]


def test_crash_response_still_has_cors_headers(client):
    """Otherwise the browser shows a misleading CORS error instead of the 500."""
    app = client.app

    @app.get("/_test/crash2")
    def _crash():
        raise ValueError("boom")

    response = client.get("/_test/crash2", headers={"Origin": "http://localhost:5173"})
    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_preflight_allowed_origin(client):
    response = client.options(
        "/v1/me",
        headers={
            "Origin": "https://insightflow.example.com",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://insightflow.example.com"


def test_cors_preflight_unknown_origin_is_refused(client):
    response = client.options(
        "/v1/me",
        headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in response.headers


def test_cors_exposes_download_filename_header(client):
    response = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert "Content-Disposition" in response.headers["access-control-expose-headers"]
