"""Every way a token can be wrong must produce a clean 401, never a 500."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.core.auth import TokenVerifier


def _code(response) -> str:
    return response.json()["error"]["code"]


def test_missing_header_is_401(client):
    response = client.get("/v1/me")
    assert response.status_code == 401
    assert _code(response) == "unauthorized"
    assert response.headers["www-authenticate"] == "Bearer"


def test_non_bearer_scheme_is_401(client):
    response = client.get("/v1/me", headers={"Authorization": "Basic abc"})
    assert response.status_code == 401


def test_garbage_token_is_401(client):
    response = client.get("/v1/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert response.status_code == 401
    assert _code(response) == "invalid_token"


def test_expired_token_is_401_with_specific_code(client, auth_header):
    long_ago = int(time.time()) - 7200
    response = client.get("/v1/me", headers=auth_header(iat=long_ago, exp=long_ago + 60))
    assert response.status_code == 401
    assert _code(response) == "token_expired"


def test_small_clock_skew_is_tolerated(client, auth_header):
    just_expired = int(time.time()) - 5
    assert client.get("/v1/me", headers=auth_header(exp=just_expired)).status_code == 200


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "anon"},  # wrong audience
        {"iss": "https://evil.supabase.co/auth/v1"},  # another project
        {"role": "anon"},  # anonymous role
        {"role": "service_role"},  # service role token must not log in as a user
        {"sub": "not-a-uuid"},
        {"sub": None},  # missing subject
        {"exp": None},  # no expiry
    ],
)
def test_bad_claims_are_401(client, auth_header, overrides):
    response = client.get("/v1/me", headers=auth_header(**overrides))
    assert response.status_code == 401, overrides


def test_token_signed_by_another_key_is_401(client, make_token):
    attacker_key = ec.generate_private_key(ec.SECP256R1())
    token = make_token(key=attacker_key)
    response = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_alg_none_is_rejected(client):
    token = jwt.encode(
        {"sub": "7c1a3c5e-2b1f-4a4e-9a51-0f6e2a7c9d10", "aud": "authenticated", "role": "authenticated"},
        key=None,
        algorithm="none",
    )
    response = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_hs256_rejected_when_no_secret_configured(client, make_token):
    token = make_token(key="some-shared-secret-that-is-long-enough-123", algorithm="HS256")
    response = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_hs256_accepted_when_legacy_secret_configured(make_token):
    secret = "legacy-project-jwt-secret-at-least-32-chars"
    verifier = TokenVerifier(
        issuer="https://test-project.supabase.co/auth/v1",
        audience="authenticated",
        key_resolver=None,
        hs256_secret=secret,
    )
    user = verifier.verify(make_token(key=secret, algorithm="HS256"))
    assert user.email == "student@example.com"


def test_auth_service_outage_is_503_not_401(settings, make_token):
    """If Supabase's key endpoint is down, don't log the user out."""
    from fastapi.testclient import TestClient

    from app.core.errors import ServiceUnavailableError
    from app.main import create_app

    def unreachable(_token):
        raise ServiceUnavailableError("down", code="auth_unavailable")

    verifier = TokenVerifier(
        issuer="https://test-project.supabase.co/auth/v1",
        audience="authenticated",
        key_resolver=unreachable,
    )
    app = create_app(settings=settings, token_verifier=verifier)
    with TestClient(app) as client:
        response = client.get("/v1/me", headers={"Authorization": f"Bearer {make_token()}"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "auth_unavailable"
