"""Authentication and role checks for the HTTP API.

The API accepts short-lived HMAC-signed bearer tokens for service-to-service
calls and keeps the legacy API key only as an explicitly scoped compatibility
mechanism.  No external JWT dependency is required.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Iterable

from fastapi import HTTPException, status

READ_ONLY = "read_only"
ORDER_REVIEW = "order_review"
SIGNAL_EXECUTION = "signal_execution"
EMERGENCY_STOP = "emergency_stop"
MODEL_MANAGEMENT = "model_management"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_base64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(frozen=True)
class Principal:
    """Authenticated caller and its granted roles."""

    subject: str
    roles: frozenset[str]
    token_id: str | None = None

    def has(self, role: str) -> bool:
        return role in self.roles


class AuthenticationService:
    """Validate bearer tokens and explicitly scoped static API keys."""

    def __init__(self, *, environment: dict[str, str] | None = None) -> None:
        env = environment or os.environ
        self.jwt_secret = env.get("API_AUTH_JWT_SECRET", "").strip().encode("utf-8")
        self.issuer = env.get("API_AUTH_JWT_ISSUER", "smart-mt5-api").strip()
        try:
            self.max_token_age = int(env.get("API_AUTH_TOKEN_TTL_SECONDS", "300"))
        except ValueError as error:
            raise ValueError("API_AUTH_TOKEN_TTL_SECONDS must be an integer") from error
        if self.max_token_age < 30 or self.max_token_age > 3600:
            raise ValueError("API_AUTH_TOKEN_TTL_SECONDS must be between 30 and 3600")

        self.static_keys: dict[str, Principal] = {}
        self._add_static_key(
            env.get("API_AUTH_TOKEN", ""),
            "legacy",
            {
                READ_ONLY,
                SIGNAL_EXECUTION,
            },
        )
        self._add_static_key(
            env.get("API_AUTH_READ_TOKEN", ""),
            "read-service",
            {
                READ_ONLY,
            },
        )
        self._add_static_key(
            env.get("API_AUTH_ORDER_REVIEW_TOKEN", ""),
            "order-review-service",
            {
                ORDER_REVIEW,
            },
        )
        self._add_static_key(
            env.get("API_AUTH_EXECUTION_TOKEN", ""),
            "execution-service",
            {
                READ_ONLY,
                SIGNAL_EXECUTION,
            },
        )
        self._add_static_key(
            env.get("API_AUTH_OPERATOR_TOKEN", ""),
            "operator",
            {
                READ_ONLY,
                EMERGENCY_STOP,
            },
        )
        self._add_static_key(
            env.get("API_AUTH_MODEL_TOKEN", ""),
            "model-service",
            {
                READ_ONLY,
                MODEL_MANAGEMENT,
            },
        )

    def _add_static_key(self, value: str, subject: str, roles: Iterable[str]) -> None:
        if value.strip():
            self.static_keys[value.strip()] = Principal(subject, frozenset(roles))

    def issue_token(
        self,
        *,
        subject: str,
        roles: Iterable[str],
        token_id: str,
        now: int | None = None,
    ) -> str:
        """Create a short-lived token for an already authenticated operator."""
        if not self.jwt_secret:
            raise RuntimeError("API_AUTH_JWT_SECRET is not configured")
        issued_at = int(time.time() if now is None else now)
        header = _base64url(b'{"alg":"HS256","typ":"JWT"}')
        payload = _base64url(
            json.dumps(
                {
                    "iss": self.issuer,
                    "sub": subject,
                    "roles": sorted(set(roles)),
                    "iat": issued_at,
                    "exp": issued_at + self.max_token_age,
                    "jti": token_id,
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        unsigned = f"{header}.{payload}".encode("ascii")
        signature = _base64url(
            hmac.new(self.jwt_secret, unsigned, hashlib.sha256).digest()
        )
        return f"{header}.{payload}.{signature}"

    def authenticate(
        self, *, api_key: str | None, authorization: str | None
    ) -> Principal | None:
        """Return the caller or ``None`` when no credentials were supplied."""
        if authorization:
            if not authorization.startswith("Bearer "):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="invalid authorization scheme",
                )
            return self._verify_bearer(authorization[7:].strip())
        if api_key:
            principal = self.static_keys.get(api_key)
            if principal is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized"
                )
            return principal
        return None

    def _verify_bearer(self, token: str) -> Principal:
        if not self.jwt_secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="bearer authentication is not configured",
            )
        parts = token.split(".")
        if len(parts) != 3:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token"
            )
        unsigned = f"{parts[0]}.{parts[1]}".encode("ascii")
        expected = _base64url(
            hmac.new(self.jwt_secret, unsigned, hashlib.sha256).digest()
        )
        if not hmac.compare_digest(expected, parts[2]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token"
            )
        try:
            header = json.loads(_decode_base64url(parts[0]))
            claims: dict[str, Any] = json.loads(_decode_base64url(parts[1]))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token"
            ) from error
        now = int(time.time())
        if header.get("alg") != "HS256" or header.get("typ") != "JWT":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token"
            )
        if claims.get("iss") != self.issuer or not isinstance(claims.get("sub"), str):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token"
            )
        if not isinstance(claims.get("iat"), int) or not isinstance(
            claims.get("exp"), int
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token"
            )
        if (
            claims["iat"] > now + 30
            or claims["exp"] <= now
            or claims["exp"] - claims["iat"] > self.max_token_age
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="expired bearer token"
            )
        roles = claims.get("roles")
        if not isinstance(roles, list) or not all(
            isinstance(role, str) for role in roles
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer roles"
            )
        return Principal(claims["sub"], frozenset(roles), claims.get("jti"))


def require_role(
    service: AuthenticationService,
    *,
    api_key: str | None,
    authorization: str | None,
    role: str,
    require_configured: bool = False,
) -> Principal:
    """Authenticate a request and require one explicit role."""
    principal = service.authenticate(api_key=api_key, authorization=authorization)
    if principal is None:
        if require_configured:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
            )
        return Principal("anonymous", frozenset())
    if not principal.has(role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="insufficient role"
        )
    return principal
