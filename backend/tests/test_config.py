import pytest
from pydantic import ValidationError

from app.config import Settings


def test_derived_urls():
    s = Settings(_env_file=None, supabase_url="https://abc.supabase.co/", app_env="test")
    assert s.jwks_url == "https://abc.supabase.co/auth/v1/.well-known/jwks.json"
    assert s.jwt_issuer == "https://abc.supabase.co/auth/v1"
    assert s.max_upload_bytes == 25 * 1024 * 1024


def test_origins_are_parsed_from_comma_list():
    s = Settings(_env_file=None, supabase_url="https://abc.supabase.co", allowed_origins=" https://a.app/ , http://localhost:5173 ")
    assert s.cors_origins == ["https://a.app", "http://localhost:5173"]


def test_wildcard_origin_refused_in_production():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, supabase_url="https://abc.supabase.co", app_env="production", allowed_origins="*")


def test_supabase_url_must_be_a_url():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, supabase_url="abc.supabase.co")


def test_docs_hidden_in_production():
    s = Settings(
        _env_file=None, supabase_url="https://abc.supabase.co", app_env="production", allowed_origins="https://a.app",
        database_url="postgresql://x", supabase_secret_key="sb_secret_x",
    )
    assert s.docs_enabled is False


def test_production_requires_secrets():
    with pytest.raises(ValidationError, match="required in production"):
        Settings(_env_file=None, supabase_url="https://abc.supabase.co", app_env="production", allowed_origins="https://a.app")
