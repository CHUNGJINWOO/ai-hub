"""
Tests for REST authorization middleware and the shared token_verifier module.

Covers:
- authorization_context_from_claims() contract (mirrors MCP claim rules)
- verify_rest_token() with static API key
- verify_rest_token() with invalid / missing tokens
- RestAuthorizationMiddleware injection via TestClient
- /context/search and /context/assemble authorization behaviour
- /healthz bypasses authorization
- MCP and REST produce the same AuthorizationContext from the same claims
"""

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("MCP_ACCESS_TOKEN", "test-only-mcp-token")

from fastapi.testclient import TestClient

from app.core.authorization import (
    AuthorizationContext,
    AuthorizationDenied,
)
from app.core.token_verifier import (
    authorization_context_from_claims,
    verify_rest_token,
)
from app.main import app, _REST_API_KEY
from app.routers import context as context_router

# Use the actual key that the middleware was initialised with so that
# header-based tests produce a verified context.
_TEST_API_KEY: str = _REST_API_KEY or "test-only-mcp-token"


# ---------------------------------------------------------------------------
# authorization_context_from_claims — shared conversion rules
# ---------------------------------------------------------------------------

class AuthorizationContextFromClaimsTests(unittest.TestCase):
    def test_static_api_key_method_grants_global_read(self):
        ctx = authorization_context_from_claims(
            identity="static-api-key",
            claims={"auth_method": "static_api_key"},
        )
        self.assertEqual(ctx.identity, "static-api-key")
        self.assertTrue(ctx.allow_global_read)
        self.assertFalse(ctx.allow_write)
        self.assertEqual(ctx.allowed_project_ids, frozenset())

    def test_allow_global_read_claim_is_respected(self):
        ctx = authorization_context_from_claims(
            identity="user-1",
            claims={"allow_global_read": True},
        )
        self.assertTrue(ctx.allow_global_read)

    def test_allow_write_claim_is_respected(self):
        ctx = authorization_context_from_claims(
            identity="user-1",
            claims={"allow_write": True},
        )
        self.assertTrue(ctx.allow_write)

    def test_allowed_project_ids_int_and_str_are_coerced(self):
        ctx = authorization_context_from_claims(
            identity="user-1",
            claims={"allowed_project_ids": [7, "8"]},
        )
        self.assertEqual(ctx.allowed_project_ids, frozenset({7, 8}))

    def test_empty_project_ids_is_valid(self):
        ctx = authorization_context_from_claims(
            identity="user-1",
            claims={},
        )
        self.assertEqual(ctx.allowed_project_ids, frozenset())
        self.assertFalse(ctx.allow_global_read)

    def test_missing_identity_raises_denied(self):
        with self.assertRaisesRegex(
            AuthorizationDenied,
            "does not contain a principal",
        ):
            authorization_context_from_claims(identity=None, claims={})

    def test_empty_identity_raises_denied(self):
        with self.assertRaisesRegex(
            AuthorizationDenied,
            "does not contain a principal",
        ):
            authorization_context_from_claims(identity="", claims={})

    def test_non_list_project_ids_raises_denied(self):
        with self.assertRaisesRegex(
            AuthorizationDenied,
            "invalid project permissions",
        ):
            authorization_context_from_claims(
                identity="user-1",
                claims={"allowed_project_ids": "not-a-list"},
            )

    def test_non_integer_project_id_raises_denied(self):
        with self.assertRaisesRegex(
            AuthorizationDenied,
            "invalid project permissions",
        ):
            authorization_context_from_claims(
                identity="user-1",
                claims={"allowed_project_ids": ["abc"]},
            )

    def test_mcp_and_rest_produce_identical_context_from_same_claims(self):
        """Regression: both paths call authorization_context_from_claims with
        equivalent inputs so the output must be equal."""
        from app.mcp_server import _authorization_context_from_access_token
        from mcp.server.auth.provider import AccessToken

        claims = {
            "allowed_project_ids": [2, "3"],
            "allow_global_read": True,
            "allow_write": True,
        }

        mcp_ctx = _authorization_context_from_access_token(
            AccessToken(
                token="tok",
                client_id="client",
                scopes=["aihub:read"],
                subject="user-42",
                claims=claims,
            )
        )
        rest_ctx = authorization_context_from_claims(
            identity="user-42",
            claims=claims,
        )

        self.assertEqual(mcp_ctx, rest_ctx)


# ---------------------------------------------------------------------------
# verify_rest_token — static API key path
# ---------------------------------------------------------------------------

class VerifyRestTokenStaticKeyTests(unittest.TestCase):
    def test_valid_static_key_returns_global_read_context(self):
        ctx = verify_rest_token(_TEST_API_KEY, api_key=_TEST_API_KEY)
        self.assertIsNotNone(ctx)
        self.assertTrue(ctx.allow_global_read)
        self.assertFalse(ctx.allow_write)
        self.assertEqual(ctx.allowed_project_ids, frozenset())

    def test_wrong_static_key_returns_none(self):
        ctx = verify_rest_token("wrong-key", api_key=_TEST_API_KEY)
        self.assertIsNone(ctx)

    def test_empty_token_returns_none(self):
        ctx = verify_rest_token("", api_key=_TEST_API_KEY)
        self.assertIsNone(ctx)

    def test_none_api_key_skips_static_path(self):
        # When no api_key is configured the static path must be bypassed.
        ctx = verify_rest_token(_TEST_API_KEY, api_key=None)
        self.assertIsNone(ctx)


# ---------------------------------------------------------------------------
# verify_rest_token — JWT path (JWKS unavailable / stubbed)
# ---------------------------------------------------------------------------

class VerifyRestTokenJWTTests(unittest.TestCase):
    def test_invalid_jwt_returns_none(self):
        ctx = verify_rest_token("not.a.jwt", api_key=None)
        self.assertIsNone(ctx)

    def test_expired_jwt_returns_none(self):
        # jwt.decode raises ExpiredSignatureError — must be swallowed.
        import jwt as pyjwt
        with patch("app.core.token_verifier._jwks_client") as mock_client:
            mock_key = MagicMock()
            mock_key.key = "secret"
            mock_client.get_signing_key_from_jwt.return_value = mock_key
            with patch("app.core.token_verifier.jwt.decode") as mock_decode:
                mock_decode.side_effect = pyjwt.ExpiredSignatureError("expired")
                ctx = verify_rest_token("some.jwt.token", api_key=None)
        self.assertIsNone(ctx)

    def test_jwt_missing_required_scope_returns_none(self):
        with patch("app.core.token_verifier._jwks_client") as mock_client:
            mock_key = MagicMock()
            mock_key.key = "secret"
            mock_client.get_signing_key_from_jwt.return_value = mock_key
            with patch("app.core.token_verifier.jwt.decode") as mock_decode:
                mock_decode.return_value = {
                    "sub": "user-7",
                    "scope": "openid",       # aihub:read absent
                    "aud": "https://ros2-server.tail49948f.ts.net:10000/mcp",
                }
                ctx = verify_rest_token("some.jwt.token", api_key=None)
        self.assertIsNone(ctx)

    def test_valid_jwt_claims_produce_authorization_context(self):
        import app.core.token_verifier as tv
        with patch.object(tv, "_jwks_client") as mock_client:
            mock_key = MagicMock()
            mock_key.key = "secret"
            mock_client.get_signing_key_from_jwt.return_value = mock_key
            with patch.object(tv, "jwt") as mock_jwt:
                mock_jwt.decode.return_value = {
                    "sub": "user-7",
                    "scope": "aihub:read",
                    "aud": tv.KEYCLOAK_AUDIENCE,
                    "allowed_project_ids": [7],
                    "allow_global_read": False,
                    "allow_write": False,
                }
                mock_jwt.ExpiredSignatureError = __import__(
                    "jwt"
                ).ExpiredSignatureError
                ctx = verify_rest_token("some.jwt.token", api_key=None)

        self.assertIsNotNone(ctx)
        self.assertEqual(ctx.identity, "user-7")
        self.assertEqual(ctx.allowed_project_ids, frozenset({7}))
        self.assertFalse(ctx.allow_global_read)

    def test_no_jwks_client_means_jwt_path_unavailable(self):
        import app.core.token_verifier as tv
        with patch.object(tv, "_jwks_client", None):
            ctx = verify_rest_token("some.jwt.token", api_key=None)
        self.assertIsNone(ctx)


# ---------------------------------------------------------------------------
# RestAuthorizationMiddleware — injection and fail-closed behaviour
# ---------------------------------------------------------------------------

class RestAuthorizationMiddlewareTests(unittest.TestCase):
    """Test the middleware via TestClient making real ASGI calls to main.app."""

    def _client_with_key(self, key: str) -> TestClient:
        return TestClient(app, headers={"Authorization": f"Bearer {key}"})

    def _client_no_auth(self) -> TestClient:
        return TestClient(app)

    # --- /healthz bypasses all authorization ---------------------------------

    def test_healthz_requires_no_auth(self):
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
        ):
            with self._client_no_auth() as client:
                response = client.get("/healthz")
        # healthz has its own DB check; it may return 200 or 503 depending on
        # DB availability, but it must never return 403.
        self.assertNotEqual(response.status_code, 403)

    # --- /context/search without auth → 403 ---------------------------------

    def test_context_search_without_auth_returns_403(self):
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch.object(context_router, "unified_search_context"),
        ):
            with self._client_no_auth() as client:
                response = client.get(
                    "/context/search", params={"q": "cmd_vel"}
                )
        self.assertEqual(response.status_code, 403)

    # --- /context/search with valid static key → 200 ------------------------

    def test_context_search_with_static_key_succeeds(self):
        search_result = {
            "query": "cmd_vel",
            "project_id": None,
            "memory_count": 0,
            "document_count": 0,
            "memories": [],
            "documents": [],
        }
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch.object(
                context_router,
                "unified_search_context",
                return_value=search_result,
            ),
        ):
            with self._client_with_key(_TEST_API_KEY) as client:
                response = client.get(
                    "/context/search", params={"q": "cmd_vel"}
                )
        self.assertEqual(response.status_code, 200)

    # --- /context/assemble with valid static key → 200 ----------------------

    def test_context_assemble_with_static_key_succeeds(self):
        assemble_result = {
            "query": "cmd_vel",
            "project_id": None,
            "memory_count": 0,
            "document_count": 0,
            "memories": [],
            "documents": [],
            "sources": [],
        }
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch.object(
                context_router,
                "build_context",
                return_value=assemble_result,
            ),
        ):
            with self._client_with_key(_TEST_API_KEY) as client:
                response = client.get(
                    "/context/assemble", params={"q": "cmd_vel"}
                )
        self.assertEqual(response.status_code, 200)

    # --- invalid token → 403 -------------------------------------------------

    def test_invalid_token_returns_403_on_guarded_route(self):
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch.object(context_router, "unified_search_context"),
        ):
            with self._client_with_key("completely-wrong-token") as client:
                response = client.get(
                    "/context/search", params={"q": "cmd_vel"}
                )
        self.assertEqual(response.status_code, 403)

    # --- project-scoped access with static key → 403 on project_id call -----

    def test_global_read_key_cannot_access_project_scoped_route(self):
        """static key has allow_global_read=True but allowed_project_ids={}.
        Accessing project_id=2 must be denied (existing contract)."""
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch.object(context_router, "unified_search_context"),
        ):
            with self._client_with_key(_TEST_API_KEY) as client:
                response = client.get(
                    "/context/search",
                    params={"q": "cmd_vel", "project_id": 2},
                )
        self.assertEqual(response.status_code, 403)

    # --- existing test_context_http.py contract: app_state injection ----------

    def test_existing_app_state_injection_still_works(self):
        """Regression: TestClient.app_state injection must continue to work
        when no Authorization header is present."""
        assemble_result = {
            "query": "cmd_vel publisher",
            "project_id": 2,
            "memory_count": 0,
            "document_count": 0,
            "memories": [],
            "documents": [],
            "sources": [],
        }
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch.object(
                context_router,
                "build_context",
                return_value=assemble_result,
            ),
        ):
            client = TestClient(app)
            client.app_state["authorization_context"] = AuthorizationContext(
                identity="test-agent",
                allowed_project_ids=frozenset({2}),
                allow_global_read=True,
            )
            with client:
                response = client.get(
                    "/context/assemble",
                    params={"q": "cmd_vel publisher", "project_id": 2},
                )
        self.assertEqual(response.status_code, 200)

    # --- middleware does not overwrite a pre-existing context ----------------

    def test_middleware_does_not_overwrite_app_state_context_when_no_header(self):
        """When no Authorization header is sent the middleware must leave
        request.state untouched, so an app_state-injected context wins."""
        assemble_result = {
            "query": "q",
            "project_id": None,
            "memory_count": 0,
            "document_count": 0,
            "memories": [],
            "documents": [],
            "sources": [],
        }
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch.object(
                context_router,
                "build_context",
                return_value=assemble_result,
            ),
        ):
            client = TestClient(app)
            # Inject a global-read context via app_state (no auth header).
            client.app_state["authorization_context"] = AuthorizationContext(
                identity="injected",
                allowed_project_ids=frozenset(),
                allow_global_read=True,
            )
            with client:
                response = client.get("/context/assemble", params={"q": "q"})
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# RestAuthorizationCoverageGapTests — verify gap behaviors across routers
# ---------------------------------------------------------------------------

class RestAuthorizationCoverageGapTests(unittest.TestCase):
    """Verify authorization coverage, fail-open gaps, write semantics, and
    pre-auth DB lookup oracle behavior across /documents, /memories, and /projects."""

    def setUp(self):
        self.client_no_auth = TestClient(app)
        self.client_static_key = TestClient(
            app, headers={"Authorization": f"Bearer {_TEST_API_KEY}"}
        )

    # --- 1 & 2. Unauthenticated requests to guarded routes fail closed (403) ---

    def test_documents_without_auth_returns_403(self):
        """Unauthenticated requests to /documents are rejected with 403."""
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
        ):
            response = self.client_no_auth.get("/documents")
        self.assertEqual(response.status_code, 403)

    def test_memories_without_auth_returns_403(self):
        """Unauthenticated requests to /memories are rejected with 403."""
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
        ):
            response = self.client_no_auth.get("/memories")
        self.assertEqual(response.status_code, 403)

    # --- 7. /projects routes now enforce authorization guards (fail-closed) ---

    def test_projects_list_without_auth_returns_403(self):
        """GET /projects enforces authorization and returns 403 without auth."""
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
        ):
            response = self.client_no_auth.get("/projects")
        self.assertEqual(response.status_code, 403)

    def test_projects_detail_without_auth_returns_403(self):
        """GET /projects/{id} enforces authorization and returns 403 without auth."""
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
        ):
            response = self.client_no_auth.get("/projects/1")
        self.assertEqual(response.status_code, 403)

    # --- 4. allow_write=False semantics (static API key cannot write) ---

    def test_static_key_denied_for_document_write(self):
        """Static API key has allow_write=False; document creation must be denied (403)."""
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
        ):
            response = self.client_static_key.post(
                "/documents",
                json={"title": "Test Doc", "project_id": 1},
            )
        self.assertEqual(response.status_code, 403)

    def test_static_key_denied_for_memory_write(self):
        """Static API key has allow_write=False; memory creation must be denied (403)."""
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
        ):
            response = self.client_static_key.post(
                "/memories",
                json={"content": "Test Memory", "project_id": 1},
            )
        self.assertEqual(response.status_code, 403)

    # --- 8. Pre-auth DB lookup oracle: 404 precedes 403 on missing resource ---

    def test_document_detail_reveals_404_before_auth_check_for_missing_item(self):
        """When an unauthenticated request accesses a non-existent document,
        the DB lookup runs before authorization, returning 404 instead of 403."""
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = None
            response = self.client_no_auth.get("/documents/999999")
        self.assertEqual(response.status_code, 404)

    def test_document_detail_returns_403_when_item_exists_in_db(self):
        """When the document exists in DB, the guard runs after DB lookup and returns 403."""
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = [1]
            response = self.client_no_auth.get("/documents/1")
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
