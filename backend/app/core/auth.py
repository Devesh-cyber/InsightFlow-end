"""
Authentication: verify the Supabase access token locally.

How it works
------------
1. The browser logs in directly with Supabase Auth (email/password or Google)
   and receives an access token (a JWT).
2. It sends that token to us in the `Authorization: Bearer <token>` header.
3. We verify the token's signature ourselves using Supabase's PUBLIC signing
   keys (the JWKS), which we download once and cache. No network call to
   Supabase per request; the old backend made two.
4. We check expiry, audience ("authenticated") and issuer (our project), and
   read the user id from the `sub` claim.

Security notes
--------------
- Only explicitly allowed algorithms are accepted. "none" is never accepted.
- The HS256 path (legacy shared secret) is only enabled if you configure
  SUPABASE_JWT_SECRET, and it is never used with a public key, which blocks
  the classic "algorithm confusion" attack.
- If Supabase's key endpoint is unreachable we return 503 (retry later), not
  401, so the frontend does not wrongly log the user out.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings
from app.core.errors import ServiceUnavailableError, UnauthorizedError
from app.core.request_context import user_id_var

logger = logging.getLogger("app.auth")

ASYMMETRIC_ALGORITHMS = frozenset({"ES256", "RS256"})
CLOCK_SKEW_SECONDS = 30


@dataclass(frozen=True, slots=True)
class AuthUser:
    id: UUID
    email: str | None


# A key resolver takes a raw token and returns the public key to verify it.
KeyResolver = Callable[[str], Any]


class TokenVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        key_resolver: KeyResolver | None,
        hs256_secret: str | None = None,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._key_resolver = key_resolver
        self._hs256_secret = hs256_secret

    @classmethod
    def from_settings(cls, settings: Settings) -> "TokenVerifier":
        jwks_client = jwt.PyJWKClient(
            settings.jwks_url,
            cache_jwk_set=True,
            lifespan=600,  # re-download the key set at most every 10 minutes
            timeout=5,
        )

        def resolve(token: str) -> Any:
            try:
                return jwks_client.get_signing_key_from_jwt(token).key
            except jwt.PyJWKClientConnectionError as exc:
                logger.error("could not fetch Supabase signing keys: %s", exc)
                raise ServiceUnavailableError(
                    "Login service is temporarily unreachable. Please retry.",
                    code="auth_unavailable",
                ) from exc
            except jwt.PyJWKClientError as exc:
                # Unknown key id even after a refresh -> token not from our project.
                raise UnauthorizedError("Invalid authentication token.", code="invalid_token") from exc

        secret = settings.supabase_jwt_secret.get_secret_value() if settings.supabase_jwt_secret else None
        return cls(
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            key_resolver=resolve,
            hs256_secret=secret,
        )

    def verify(self, token: str) -> AuthUser:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise UnauthorizedError("Malformed authentication token.", code="invalid_token") from exc

        algorithm = header.get("alg")
        if algorithm in ASYMMETRIC_ALGORITHMS and self._key_resolver is not None:
            key = self._key_resolver(token)
        elif algorithm == "HS256" and self._hs256_secret:
            key = self._hs256_secret
        else:
            raise UnauthorizedError("Unsupported token signature.", code="invalid_token")

        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[algorithm],
                audience=self._audience,
                issuer=self._issuer,
                leeway=CLOCK_SKEW_SECONDS,
                options={"require": ["exp", "sub", "aud", "iss"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise UnauthorizedError("Your session has expired. Please log in again.", code="token_expired") from exc
        except jwt.InvalidTokenError as exc:
            raise UnauthorizedError("Invalid authentication token.", code="invalid_token") from exc

        if claims.get("role") != "authenticated":
            raise UnauthorizedError("This token cannot access the API.", code="invalid_token")

        try:
            user_id = UUID(str(claims["sub"]))
        except ValueError as exc:
            raise UnauthorizedError("Invalid authentication token.", code="invalid_token") from exc

        return AuthUser(id=user_id, email=claims.get("email"))


_bearer = HTTPBearer(auto_error=False)


def get_token_verifier(request: Request) -> TokenVerifier:
    return request.app.state.token_verifier


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    verifier: TokenVerifier = Depends(get_token_verifier),
) -> AuthUser:
    """FastAPI dependency: the logged-in user, or a 401."""
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise UnauthorizedError()

    user = verifier.verify(credentials.credentials)
    user_id_var.set(str(user.id))  # tag all log lines for this request
    return user
