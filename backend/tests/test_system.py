def test_health_is_public_and_fast(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_supports_head_for_render(client):
    assert client.head("/health").status_code == 200


def test_every_response_has_request_id_and_security_headers(client):
    response = client.get("/health")
    assert len(response.headers["x-request-id"]) == 32
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_safe_incoming_request_id_is_kept(client):
    response = client.get("/health", headers={"X-Request-ID": "frontend-abc-123"})
    assert response.headers["x-request-id"] == "frontend-abc-123"


def test_unsafe_incoming_request_id_is_replaced(client):
    response = client.get("/health", headers={"X-Request-ID": "bad id\nwith newline"})
    assert response.headers["x-request-id"] != "bad id\nwith newline"


def test_me_returns_user_from_token(client, auth_header):
    response = client.get("/v1/me", headers=auth_header(sub="7c1a3c5e-2b1f-4a4e-9a51-0f6e2a7c9d10"))
    assert response.status_code == 200
    assert response.json() == {
        "user_id": "7c1a3c5e-2b1f-4a4e-9a51-0f6e2a7c9d10",
        "email": "student@example.com",
        "usage": None,  # datasets not configured in this app
    }


def test_health_has_a_single_operation_in_openapi(client):
    """Regression: GET+HEAD on one function produced a duplicate operation id warning."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        schema = client.get("/openapi.json").json()
    assert list(schema["paths"]["/health"]) == ["get"]
