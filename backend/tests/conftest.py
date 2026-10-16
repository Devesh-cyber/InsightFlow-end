"""
Shared test fixtures.

Tests never talk to the real Supabase. Instead we generate our own ES256 key
pair, sign tokens with it, and give the app a verifier that trusts that key.
This exercises the exact same verification code path as production.
"""

import os
import time
import uuid
from collections.abc import Callable

# Must be set before `app.main` is imported anywhere (it builds a default app).
os.environ.setdefault("SUPABASE_URL", "https://test-project.supabase.co")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:5173")

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.auth import TokenVerifier
from app.main import create_app

ISSUER = "https://test-project.supabase.co/auth/v1"


@pytest.fixture(scope="session")
def signing_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        file_cache_dir=str(tmp_path / "file-cache"),
        supabase_url="https://test-project.supabase.co",
        app_env="test",
        allowed_origins="http://localhost:5173,https://insightflow.example.com",
    )


@pytest.fixture
def verifier(signing_key) -> TokenVerifier:
    public_key = signing_key.public_key()
    return TokenVerifier(
        issuer=ISSUER,
        audience="authenticated",
        key_resolver=lambda _token: public_key,
    )


@pytest.fixture
def client(settings, verifier) -> TestClient:
    """App WITHOUT datasets configured (like Phase 0)."""
    app = create_app(settings=settings, token_verifier=verifier)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def fake_repo():
    from tests.fakes import FakeRepository

    return FakeRepository()


@pytest.fixture
def fake_storage(tmp_path):
    from tests.fakes import FakeStorage

    root = tmp_path / "storage"
    root.mkdir()
    return FakeStorage(root)


@pytest.fixture
def dataset_service(fake_repo, fake_storage, settings):
    from app.services.datasets import DatasetLimits, DatasetService

    return DatasetService(
        fake_repo,
        fake_storage,
        DatasetLimits(max_datasets=settings.max_datasets_per_user, keep_versions=settings.max_versions_per_dataset),
    )


@pytest.fixture
def api(settings, verifier, dataset_service) -> TestClient:
    """App WITH datasets, backed by in-memory fakes."""
    app = create_app(settings=settings, token_verifier=verifier, dataset_service=dataset_service)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


USER_A = "11111111-1111-4111-8111-111111111111"
USER_B = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def make_token(signing_key) -> Callable[..., str]:
    """Build a Supabase-shaped access token. Override any claim via kwargs."""

    def _make(key=None, algorithm: str = "ES256", headers: dict | None = None, **overrides) -> str:
        now = int(time.time())
        claims = {
            "sub": str(uuid.uuid4()),
            "email": "student@example.com",
            "aud": "authenticated",
            "iss": ISSUER,
            "role": "authenticated",
            "iat": now,
            "exp": now + 3600,
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, key or signing_key, algorithm=algorithm, headers=headers)

    return _make


@pytest.fixture
def auth_header(make_token) -> Callable[..., dict[str, str]]:
    def _header(**overrides) -> dict[str, str]:
        return {"Authorization": f"Bearer {make_token(**overrides)}"}

    return _header
