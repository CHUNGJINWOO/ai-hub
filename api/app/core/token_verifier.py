"""
Shared token verification and AuthorizationContext construction.

Both the REST middleware and the MCP server use the same claims-to-context
conversion rules.  Neither side of this module imports from mcp_server or
from MCP SDK types.
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Any

import jwt
from jwt import ExpiredSignatureError, PyJWKClient

from app.core.authorization import (
    AuthorizationContext,
    AuthorizationDenied,
    claims_to_authorization_context,
)
from app.core.capabilities import Capability

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

    Delegates to claims_to_authorization_context so that standard claims
    (read_project_ids, write_project_ids, capabilities) and legacy claims
    (allowed_project_ids, allow_write, allow_global_read) are parsed identically
    across REST and MCP services.
    """
    return claims_to_authorization_context(identity=identity, claims=claims)


# ---------------------------------------------------------------------------
# Static service identity configuration
# ---------------------------------------------------------------------------

def get_static_key_context() -> AuthorizationContext:
    """Build AuthorizationContext for the configured static service identity."""
    identity = os.getenv("AIHUB_STATIC_KEY_IDENTITY", "service:static-api-key")
    if not identity or not identity.strip():
        identity = "service:static-api-key"

    raw_read = os.getenv("AIHUB_STATIC_KEY_READ_PROJECTS", "")
    read_pids: set[int] = set()
    if raw_read:
        for val in raw_read.split(","):
            val = val.strip()
            if val:
                try:
                    read_pids.add(int(val))
                except (ValueError, TypeError):
                    pass

    raw_write = os.getenv("AIHUB_STATIC_KEY_WRITE_PROJECTS", "")
    write_pids: set[int] = set()
    if raw_write:
        for val in raw_write.split(","):
            val = val.strip()
            if val:
                try:
                    write_pids.add(int(val))
                except (ValueError, TypeError):
                    pass

    raw_caps = os.getenv("AIHUB_STATIC_KEY_CAPABILITIES", "")
    caps: set[Capability] = set()
    if raw_caps:
        for val in raw_caps.split(","):
            val = val.strip()
            if val:
                try:
                    caps.add(Capability(val))
                except (ValueError, KeyError):
                    pass
    else:
        # Default capability when unconfigured: RESOURCE_READ_GLOBAL
        # (Fail-closed: No PROJECT_READ_ALL, no write permissions)
        caps.add(Capability.RESOURCE_READ_GLOBAL)

    # Invariant: write_project_ids must be a subset of read_project_ids
    if not write_pids.issubset(read_pids):
        read_pids = read_pids | write_pids

    return AuthorizationContext(
        identity=identity,
        read_project_ids=frozenset(read_pids),
        write_project_ids=frozenset(write_pids),
        capabilities=frozenset(caps),
    )


# ---------------------------------------------------------------------------
# Token verification: raw token string → AuthorizationContext | None
# Used by the REST middleware.  Returns None on any verification failure so
# the middleware can leave request.state unset and let the guard fail-closed.
# ---------------------------------------------------------------------------

def verify_rest_token(
    raw_token: str,
    api_key: str | None = None,
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
    if api_key and hmac.compare_digest(
        raw_token.encode("utf-8"),
        api_key.encode("utf-8"),
    ):
        return get_static_key_context()

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
