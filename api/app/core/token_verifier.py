"""
Shared token verification and AuthorizationContext construction.

Both the REST middleware and the MCP server use the same claims-to-context
conversion rules.  Neither side of this module imports from mcp_server or
from MCP SDK types.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import jwt
from jwt import ExpiredSignatureError, PyJWKClient

from app.core.authorization import AuthorizationContext, AuthorizationDenied

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment – read once at module import time (safe: no side-effects).
# The defaults mirror mcp_server.py so both services see the same issuer.
# ---------------------------------------------------------------------------

KEYCLOAK_JWKS_URL: str = os.getenv(
    "KEYCLOAK_JWKS_URL",
    "http://keycloak:8080/realms/ai-hub/protocol/openid-connect/certs",
)
KEYCLOAK_ISSUER: str = os.getenv(
    "KEYCLOAK_ISSUER",
    "https://ros2-server.tail49948f.ts.net:8443/realms/ai-hub",
)
# MCP_RESOURCE_URL doubles as the JWT audience for both MCP and REST.
KEYCLOAK_AUDIENCE: str = os.getenv(
    "MCP_RESOURCE_URL",
    "https://ros2-server.tail49948f.ts.net:10000/mcp",
)
REQUIRED_SCOPE: str = "aihub:read"

# Module-level singleton so the JWKS cache is shared across requests.
# Construction is lazy-safe: PyJWKClient.__init__ makes no network calls.
_jwks_client: PyJWKClient | None = (
    PyJWKClient(KEYCLOAK_JWKS_URL) if KEYCLOAK_JWKS_URL else None
)


# ---------------------------------------------------------------------------
# Core conversion: claims dict → AuthorizationContext
# This is the single authoritative place for claims interpretation.
# Both MCP (via mcp_server._authorization_context_from_access_token) and REST
# (via verify_rest_token) ultimately call this function.
# ---------------------------------------------------------------------------

def authorization_context_from_claims(
    identity: str | None,
    claims: dict[str, Any],
) -> AuthorizationContext:
    """Translate a verified principal identity and claims into an
    AuthorizationContext.

    Raises AuthorizationDenied when the token carries no usable principal or
    when the project-id claim is structurally invalid.
    """
    if not identity:
        raise AuthorizationDenied(
            "authenticated token does not contain a principal"
        )

    raw_project_ids = claims.get("allowed_project_ids", ())
    if not isinstance(raw_project_ids, (list, tuple, set, frozenset)):
        raise AuthorizationDenied(
            "authenticated token contains invalid project permissions"
        )

    try:
        allowed_project_ids = frozenset(
            int(project_id) for project_id in raw_project_ids
        )
    except (TypeError, ValueError) as exc:
        raise AuthorizationDenied(
            "authenticated token contains invalid project permissions"
        ) from exc

    auth_method = claims.get("auth_method")
    allow_global_read: bool = (
        auth_method == "static_api_key"
        or claims.get("allow_global_read") is True
    )
    allow_write: bool = claims.get("allow_write") is True

    return AuthorizationContext(
        identity=str(identity),
        allowed_project_ids=allowed_project_ids,
        allow_global_read=allow_global_read,
        allow_write=allow_write,
    )


# ---------------------------------------------------------------------------
# Token verification: raw token string → AuthorizationContext | None
# Used by the REST middleware.  Returns None on any verification failure so
# the middleware can leave request.state unset and let the guard fail-closed.
# ---------------------------------------------------------------------------

def verify_rest_token(
    raw_token: str,
    api_key: str | None,
) -> AuthorizationContext | None:
    """Verify *raw_token* against the static API key or the Keycloak JWKS.

    Returns an AuthorizationContext on success or None on any failure.
    Does not raise (aside from programming errors): all authentication
    failures are swallowed and logged so the caller stays in control of the
    HTTP response.
    """
    if not raw_token:
        return None

    # --- Static API key path ------------------------------------------
    if api_key and raw_token == api_key:
        return authorization_context_from_claims(
            identity="static-api-key",
            claims={"auth_method": "static_api_key"},
        )

    # --- Keycloak JWT path --------------------------------------------
    if _jwks_client is None:
        # JWKS URL not configured — JWT path unavailable.
        return None

    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(raw_token)

        claims: dict[str, Any] = jwt.decode(
            raw_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=KEYCLOAK_AUDIENCE,
            issuer=KEYCLOAK_ISSUER,
            options={
                "require": ["exp", "iat", "iss", "sub"],
            },
        )

        raw_scope = claims.get("scope", "")
        scopes = raw_scope.split() if isinstance(raw_scope, str) else []
        if REQUIRED_SCOPE not in scopes:
            return None

        raw_aud = claims.get("aud")
        if isinstance(raw_aud, str):
            audiences = [raw_aud]
        elif isinstance(raw_aud, list):
            audiences = [str(v) for v in raw_aud]
        else:
            return None

        if KEYCLOAK_AUDIENCE not in audiences:
            return None

        identity = str(claims.get("sub", ""))
        return authorization_context_from_claims(identity=identity, claims=claims)

    except ExpiredSignatureError:
        logger.debug("REST auth: JWT has expired")
        return None
    except AuthorizationDenied as exc:
        logger.debug("REST auth: claims rejected: %s", exc)
        return None
    except Exception:
        # Covers PyJWTError, PyJWKClientError, network errors, etc.
        return None
