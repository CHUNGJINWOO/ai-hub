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
from app.routers import documents as documents_router
from app.routers import memories as memories_router

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

    # --- 8. Pre-auth DB lookup oracle mitigated: 403 precedes DB lookup ---

    def test_document_detail_reveals_404_before_auth_check_for_missing_item(self):
        """When an unauthenticated request accesses a non-existent document,
        authorization fails before DB lookup, returning 403 without querying the DB."""
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            response = self.client_no_auth.get("/documents/999999")
        self.assertEqual(response.status_code, 403)
        mock_db.assert_not_called()

    def test_document_detail_returns_403_when_item_exists_in_db(self):
        """When the document exists in DB, unauthenticated request returns 403 before DB lookup."""
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            response = self.client_no_auth.get("/documents/1")
        self.assertEqual(response.status_code, 403)
        mock_db.assert_not_called()


# ---------------------------------------------------------------------------
# DocumentPatchAuthorizationTests — verify cross-project PATCH hijacking prevention
# ---------------------------------------------------------------------------

class DocumentPatchAuthorizationTests(unittest.TestCase):
    """Regression tests for PATCH /documents/{id} cross-project authorization."""

    def _client_with_context(self, context: AuthorizationContext) -> TestClient:
        client = TestClient(app)
        client.app_state["authorization_context"] = context
        return client

    # Case A: authorized source project + normal PATCH -> success
    def test_case_a_authorized_source_project_normal_patch_succeeds(self):
        """Case A: Caller with write access to document's source project updates title successfully."""
        context = AuthorizationContext(
            identity="user-p1",
            allowed_project_ids=frozenset({1}),
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.side_effect = [
                [1, "Old Title", "doc.txt", "text/plain", "manual", "desc", "active"],  # current
                (1,),  # project existence check
                (1, 1, "New Title", "doc.txt", "text/plain", "manual", "desc", "active", "now", "now"),  # RETURNING
            ]
            client = self._client_with_context(context)
            with client:
                response = client.patch(
                    "/documents/1",
                    json={"title": "New Title"},
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["title"], "New Title")
        self.assertEqual(response.json()["project_id"], 1)
        mock_conn.commit.assert_called_once()

    # Case B: unauthorized source project + normal PATCH -> 403
    def test_case_b_unauthorized_source_project_normal_patch_denied(self):
        """Case B: Caller without write access to document's source project is rejected with 403."""
        context = AuthorizationContext(
            identity="user-p2",
            allowed_project_ids=frozenset({2}),
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = [
                1, "Old Title", "doc.txt", "text/plain", "manual", "desc", "active"
            ]
            client = self._client_with_context(context)
            with client:
                response = client.patch(
                    "/documents/1",
                    json={"title": "New Title"},
                )
        self.assertEqual(response.status_code, 403)
        self.assertIn("not authorized for project: 1", response.json()["detail"])
        # Ensure UPDATE was never executed
        self.assertFalse(
            any("UPDATE documents" in " ".join(call[0][0].split()) for call in mock_conn.execute.call_args_list)
        )
        mock_conn.commit.assert_not_called()

    # Case C (Crucial!): unauthorized source project + authorized target project -> 403
    def test_case_c_cross_project_patch_hijacking_prevented(self):
        """Case C: Attacker authorized for target project (2) but not source project (1) cannot hijack document."""
        context = AuthorizationContext(
            identity="attacker-p2",
            allowed_project_ids=frozenset({2}),
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            # Document 1 belongs to project 1
            mock_conn.execute.return_value.fetchone.return_value = [
                1, "Secret Doc", "secret.txt", "text/plain", "manual", "confidential", "active"
            ]
            client = self._client_with_context(context)
            with client:
                response = client.patch(
                    "/documents/1",
                    json={"project_id": 2, "title": "Hijacked Title"},
                )
        self.assertEqual(response.status_code, 403)
        # Must fail at source project authorization before checking target
        self.assertIn("not authorized for project: 1", response.json()["detail"])
        self.assertFalse(
            any("UPDATE documents" in " ".join(call[0][0].split()) for call in mock_conn.execute.call_args_list)
        )
        mock_conn.commit.assert_not_called()

    # Case D: both source and target projects authorized -> success (project moved)
    def test_case_d_both_projects_authorized_moves_project(self):
        """Case D: Caller authorized for both source (1) and target (2) can move document to target project."""
        context = AuthorizationContext(
            identity="admin-p1-p2",
            allowed_project_ids=frozenset({1, 2}),
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.side_effect = [
                [1, "Title", "doc.txt", "text/plain", "manual", "desc", "active"],  # current
                (2,),  # target project 2 exists in DB
                (1, 2, "Title", "doc.txt", "text/plain", "manual", "desc", "active", "now", "now"),  # RETURNING
            ]
            client = self._client_with_context(context)
            with client:
                response = client.patch(
                    "/documents/1",
                    json={"project_id": 2},
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["project_id"], 2)
        mock_conn.commit.assert_called_once()
        # Verify the UPDATE query updated project_id to 2
        update_calls = [
            call for call in mock_conn.execute.call_args_list
            if "UPDATE documents" in " ".join(call[0][0].split())
        ]
        self.assertEqual(len(update_calls), 1)
        self.assertEqual(update_calls[0][0][1][0], 2)

    # Case D2 (Supplementary): authorized source project + unauthorized target project -> 403
    def test_case_d2_authorized_source_unauthorized_target_denied(self):
        """Case D2: Caller authorized for source project (1) but unauthorized for target project (2) is denied."""
        context = AuthorizationContext(
            identity="user-p1-only",
            allowed_project_ids=frozenset({1}),
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = [
                1, "Title", "doc.txt", "text/plain", "manual", "desc", "active"
            ]
            client = self._client_with_context(context)
            with client:
                response = client.patch(
                    "/documents/1",
                    json={"project_id": 2},
                )
        self.assertEqual(response.status_code, 403)
        self.assertIn("not authorized for project: 2", response.json()["detail"])
        mock_conn.commit.assert_not_called()

    # Case E: omitted project_id in PATCH body -> preserves existing source project
    def test_case_e_omitted_project_id_preserves_source_project(self):
        """Case E: PATCH without project_id preserves existing project_id."""
        context = AuthorizationContext(
            identity="user-p1",
            allowed_project_ids=frozenset({1}),
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.side_effect = [
                [1, "Title", "doc.txt", "text/plain", "manual", "desc", "active"],  # current
                (1,),  # project existence check
                (1, 1, "Title", "doc.txt", "text/plain", "manual", "updated desc", "active", "now", "now"),  # RETURNING
            ]
            client = self._client_with_context(context)
            with client:
                response = client.patch(
                    "/documents/1",
                    json={"description": "updated desc"},
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["project_id"], 1)
        mock_conn.commit.assert_called_once()

    # Case F: project_id explicitly None/null -> retains existing source project per DocumentUpdate semantics
    def test_case_f_null_project_id_preserves_source_project(self):
        """Case F: Explicit null project_id falls back to existing project_id per DocumentUpdate semantics."""
        context = AuthorizationContext(
            identity="user-p1",
            allowed_project_ids=frozenset({1}),
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.side_effect = [
                [1, "Title", "doc.txt", "text/plain", "manual", "desc", "active"],  # current
                (1,),  # project existence check for 1
                (1, 1, "Updated Title", "doc.txt", "text/plain", "manual", "desc", "active", "now", "now"),  # RETURNING
            ]
            client = self._client_with_context(context)
            with client:
                response = client.patch(
                    "/documents/1",
                    json={"project_id": None, "title": "Updated Title"},
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["project_id"], 1)
        mock_conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
# MemoryPatchAuthorizationTests — verify cross-project PATCH hijacking prevention
# ---------------------------------------------------------------------------

class MemoryPatchAuthorizationTests(unittest.TestCase):
    """Regression tests for PATCH /memories/{id} cross-project authorization."""

    def _client_with_context(self, context: AuthorizationContext) -> TestClient:
        client = TestClient(app)
        client.app_state["authorization_context"] = context
        return client

    # Case A: authorized source project + normal PATCH -> success
    def test_case_a_authorized_source_project_normal_patch_succeeds(self):
        """Case A: Caller with write access to memory's source project updates content successfully."""
        context = AuthorizationContext(
            identity="user-p1",
            allowed_project_ids=frozenset({1}),
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.memories.get_db_connection") as mock_db,
            patch.object(memories_router.model, "encode") as mock_encode,
        ):
            mock_encode.return_value = MagicMock(tolist=lambda: [0.1] * 384)
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.side_effect = [
                ("Old content", "fact", "cat", 3, "api", 1),  # current (index 5 is project_id)
                (1,),  # project existence check
                (1, "New content", "fact", "cat", 3, "api", "now"),  # RETURNING
            ]
            client = self._client_with_context(context)
            with client:
                response = client.patch(
                    "/memories/1",
                    json={"content": "New content"},
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "updated")
        self.assertEqual(response.json()["content"], "New content")
        mock_conn.commit.assert_called_once()

    # Case B: unauthorized source project + normal PATCH -> 403
    def test_case_b_unauthorized_source_project_normal_patch_denied(self):
        """Case B: Caller without write access to memory's source project is rejected with 403."""
        context = AuthorizationContext(
            identity="user-p2",
            allowed_project_ids=frozenset({2}),
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.memories.get_db_connection") as mock_db,
            patch.object(memories_router.model, "encode") as mock_encode,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = (
                "Old content", "fact", "cat", 3, "api", 1
            )
            client = self._client_with_context(context)
            with client:
                response = client.patch(
                    "/memories/1",
                    json={"content": "New content"},
                )
        self.assertEqual(response.status_code, 403)
        self.assertIn("not authorized for project: 1", response.json()["detail"])
        mock_encode.assert_not_called()
        self.assertFalse(
            any("UPDATE memories" in " ".join(call[0][0].split()) for call in mock_conn.execute.call_args_list)
        )
        mock_conn.commit.assert_not_called()

    # Case C (Crucial!): unauthorized source project + authorized target project -> 403
    def test_case_c_cross_project_patch_hijacking_prevented(self):
        """Case C: Attacker authorized for target project (2) but not source project (1) cannot hijack memory."""
        context = AuthorizationContext(
            identity="attacker-p2",
            allowed_project_ids=frozenset({2}),
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.memories.get_db_connection") as mock_db,
            patch.object(memories_router.model, "encode") as mock_encode,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = (
                "Secret memory", "fact", "security", 5, "api", 1
            )
            client = self._client_with_context(context)
            with client:
                response = client.patch(
                    "/memories/1",
                    json={"project_id": 2, "content": "Hijacked memory"},
                )
        self.assertEqual(response.status_code, 403)
        self.assertIn("not authorized for project: 1", response.json()["detail"])
        mock_encode.assert_not_called()
        self.assertFalse(
            any("UPDATE memories" in " ".join(call[0][0].split()) for call in mock_conn.execute.call_args_list)
        )
        mock_conn.commit.assert_not_called()

    # Case D: both source and target projects authorized -> success (project moved)
    def test_case_d_both_projects_authorized_moves_project(self):
        """Case D: Caller authorized for both source (1) and target (2) can move memory to target project."""
        context = AuthorizationContext(
            identity="admin-p1-p2",
            allowed_project_ids=frozenset({1, 2}),
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.memories.get_db_connection") as mock_db,
            patch.object(memories_router.model, "encode") as mock_encode,
        ):
            mock_encode.return_value = MagicMock(tolist=lambda: [0.1] * 384)
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.side_effect = [
                ("Content", "fact", "cat", 3, "api", 1),  # current
                (2,),  # target project 2 exists in DB
                (1, "Content", "fact", "cat", 3, "api", "now"),  # RETURNING
            ]
            client = self._client_with_context(context)
            with client:
                response = client.patch(
                    "/memories/1",
                    json={"project_id": 2},
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "updated")
        mock_conn.commit.assert_called_once()
        # Verify the UPDATE query updated project_id to 2
        update_calls = [
            call for call in mock_conn.execute.call_args_list
            if "UPDATE memories" in " ".join(call[0][0].split())
        ]
        self.assertEqual(len(update_calls), 1)
        self.assertEqual(update_calls[0][0][1][5], 2)

    # Case D2 (Supplementary): authorized source project + unauthorized target project -> 403
    def test_case_d2_authorized_source_unauthorized_target_denied(self):
        """Case D2: Caller authorized for source project (1) but unauthorized for target project (2) is denied."""
        context = AuthorizationContext(
            identity="user-p1-only",
            allowed_project_ids=frozenset({1}),
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.memories.get_db_connection") as mock_db,
            patch.object(memories_router.model, "encode") as mock_encode,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = (
                "Content", "fact", "cat", 3, "api", 1
            )
            client = self._client_with_context(context)
            with client:
                response = client.patch(
                    "/memories/1",
                    json={"project_id": 2},
                )
        self.assertEqual(response.status_code, 403)
        self.assertIn("not authorized for project: 2", response.json()["detail"])
        mock_encode.assert_not_called()
        mock_conn.commit.assert_not_called()

    # Case E: omitted project_id in PATCH body -> preserves existing source project
    def test_case_e_omitted_project_id_preserves_source_project(self):
        """Case E: PATCH without project_id preserves existing project_id."""
        context = AuthorizationContext(
            identity="user-p1",
            allowed_project_ids=frozenset({1}),
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.memories.get_db_connection") as mock_db,
            patch.object(memories_router.model, "encode") as mock_encode,
        ):
            mock_encode.return_value = MagicMock(tolist=lambda: [0.1] * 384)
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.side_effect = [
                ("Content", "fact", "cat", 3, "api", 1),  # current
                (1,),  # project existence check
                (1, "Content", "fact", "cat", 4, "api", "now"),  # RETURNING
            ]
            client = self._client_with_context(context)
            with client:
                response = client.patch(
                    "/memories/1",
                    json={"importance": 4},
                )
        self.assertEqual(response.status_code, 200)
        update_calls = [
            call for call in mock_conn.execute.call_args_list
            if "UPDATE memories" in " ".join(call[0][0].split())
        ]
        self.assertEqual(len(update_calls), 1)
        self.assertEqual(update_calls[0][0][1][5], 1)
        mock_conn.commit.assert_called_once()

    # Case F: project_id explicitly None/null -> unlinks memory (sets project_id to None per MemoryUpdate semantics)
    def test_case_f_null_project_id_unlinks_memory(self):
        """Case F: Explicit null project_id unlinks memory (sets project_id=None per MemoryUpdate semantics)."""
        context = AuthorizationContext(
            identity="user-p1",
            allowed_project_ids=frozenset({1}),
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.memories.get_db_connection") as mock_db,
            patch.object(memories_router.model, "encode") as mock_encode,
        ):
            mock_encode.return_value = MagicMock(tolist=lambda: [0.1] * 384)
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.side_effect = [
                ("Content", "fact", "cat", 3, "api", 1),  # current
                (1, "Content", "fact", "cat", 3, "api", "now"),  # RETURNING
            ]
            client = self._client_with_context(context)
            with client:
                response = client.patch(
                    "/memories/1",
                    json={"project_id": None},
                )
        self.assertEqual(response.status_code, 200)
        update_calls = [
            call for call in mock_conn.execute.call_args_list
            if "UPDATE memories" in " ".join(call[0][0].split())
        ]
        self.assertEqual(len(update_calls), 1)
        self.assertIsNone(update_calls[0][0][1][5])
        mock_conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
# DocumentChunkAuthorizationTests — verify chunk auth & DoS embedding mitigation
# ---------------------------------------------------------------------------

class DocumentChunkAuthorizationTests(unittest.TestCase):
    """Regression tests for POST /documents/{id}/chunks authorization and DoS embedding mitigation."""

    def _client_with_context(self, context: AuthorizationContext) -> TestClient:
        client = TestClient(app)
        client.app_state["authorization_context"] = context
        return client

    # Case 1: non-existent document -> 404, embedding model NOT called
    def test_chunk_nonexistent_document_returns_404_without_embedding(self):
        """Non-existent document returns 404 and does not compute embedding (prevents DoS)."""
        context = AuthorizationContext(
            identity="user-p1",
            allowed_project_ids=frozenset({1}),
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
            patch.object(documents_router.model, "encode") as mock_encode,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = None  # doc not found
            client = self._client_with_context(context)
            with client:
                response = client.post(
                    "/documents/999999/chunks",
                    json={"content": "Large chunk text that shouldn't be encoded", "chunk_index": 0},
                )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Document not found")
        mock_encode.assert_not_called()

    # Case 2a: unauthorized caller (project mismatch) -> 403, embedding model NOT called
    def test_chunk_unauthorized_project_returns_403_without_embedding(self):
        """Caller unauthorized for parent document project returns 403 and does not compute embedding."""
        context = AuthorizationContext(
            identity="user-p2",
            allowed_project_ids=frozenset({2}),
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
            patch.object(documents_router.model, "encode") as mock_encode,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            # Document 1 belongs to project 1
            mock_conn.execute.return_value.fetchone.return_value = (1, 1)
            client = self._client_with_context(context)
            with client:
                response = client.post(
                    "/documents/1/chunks",
                    json={"content": "Large chunk text that shouldn't be encoded", "chunk_index": 0},
                )
        self.assertEqual(response.status_code, 403)
        self.assertIn("not authorized for project: 1", response.json()["detail"])
        mock_encode.assert_not_called()

    # Case 2b: unauthenticated request -> 403, embedding model NOT called
    def test_chunk_unauthenticated_request_returns_403_without_embedding(self):
        """Unauthenticated request to create chunk returns 403 and does not compute embedding."""
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
            patch.object(documents_router.model, "encode") as mock_encode,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = (1, 1)
            client = TestClient(app)
            with client:
                response = client.post(
                    "/documents/1/chunks",
                    json={"content": "Large chunk text", "chunk_index": 0},
                )
        self.assertEqual(response.status_code, 403)
        mock_encode.assert_not_called()

    # Case 2c: caller with allow_write=False -> 403, embedding model NOT called
    def test_chunk_read_only_caller_returns_403_without_embedding(self):
        """Caller with allow_write=False returns 403 and does not compute embedding."""
        context = AuthorizationContext(
            identity="reader-p1",
            allowed_project_ids=frozenset({1}),
            allow_global_read=True,
            allow_write=False,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
            patch.object(documents_router.model, "encode") as mock_encode,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = (1, 1)
            client = self._client_with_context(context)
            with client:
                response = client.post(
                    "/documents/1/chunks",
                    json={"content": "Large chunk text", "chunk_index": 0},
                )
        self.assertEqual(response.status_code, 403)
        self.assertIn("not authorized for durable writes", response.json()["detail"])
        mock_encode.assert_not_called()

    # Case 3: authorized caller -> 200, embedding model called exactly once
    def test_chunk_authorized_caller_computes_embedding_and_inserts(self):
        """Authorized caller computes embedding after auth check and creates chunk."""
        context = AuthorizationContext(
            identity="writer-p1",
            allowed_project_ids=frozenset({1}),
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
            patch.object(documents_router.model, "encode") as mock_encode,
        ):
            mock_encode.return_value = MagicMock(tolist=lambda: [0.1] * 384)
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.side_effect = [
                (1, 1),  # document lookup (id=1, project_id=1)
                None,    # duplicate check
                (42, 1, 0, "Valid chunk content", 1, "now"),  # INSERT RETURNING
            ]
            client = self._client_with_context(context)
            with client:
                response = client.post(
                    "/documents/1/chunks",
                    json={"content": "Valid chunk content", "chunk_index": 0, "page_number": 1},
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "created")
        self.assertEqual(response.json()["id"], 42)
        mock_encode.assert_called_once_with("passage: Valid chunk content", normalize_embeddings=True)
        mock_conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
# IdEnumerationOracleDefenseTests — Step 2 ID Enumeration Oracle mitigation
# ---------------------------------------------------------------------------

class IdEnumerationOracleDefenseTests(unittest.TestCase):
    """Verify mitigation of ID enumeration oracle across 5 target endpoints:
    1. GET /documents/{id}
    2. GET /documents/{id}/chunks
    3. DELETE /documents/{id}
    4. GET /memories/{id}
    5. DELETE /memories/{id}

    Verifies:
    - Step 2-1: Unauthenticated requests return 403 before DB lookup (zero DB queries).
    - Step 2-2: Scoped authorization applies SQL project scoping (WHERE id = %s AND project_id = ANY(%s)).
    - Out-of-scope resources and nonexistent resources both normalize to 404.
    - Normal 404 semantics are preserved for authorized callers.
    """

    def setUp(self):
        self.client_no_auth = TestClient(app)

    def _client_with_context(self, context: AuthorizationContext) -> TestClient:
        client = TestClient(app)
        client.app_state["authorization_context"] = context
        return client

    # =========================================================================
    # 1. GET /documents/{document_id}
    # =========================================================================

    def test_get_document_unauthenticated_prevents_db_lookup_and_returns_403(self):
        """Unauthenticated GET /documents/{id} returns 403 for both missing and existing IDs without querying DB."""
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            res_missing = self.client_no_auth.get("/documents/999999")
            res_existing = self.client_no_auth.get("/documents/1")

        self.assertEqual(res_missing.status_code, 403)
        self.assertEqual(res_existing.status_code, 403)
        mock_db.assert_not_called()

    def test_get_document_scoped_user_applies_sql_filter_and_normalizes_to_404(self):
        """Scoped caller queries with SQL project filter; nonexistent and out-of-scope IDs both return 404."""
        context = AuthorizationContext(
            identity="scoped-user",
            allowed_project_ids=frozenset({2}),
            allow_global_read=False,
            allow_write=False,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = None

            client = self._client_with_context(context)
            with client:
                res_missing = client.get("/documents/999999")
                res_out_of_scope = client.get("/documents/1")

        self.assertEqual(res_missing.status_code, 404)
        self.assertEqual(res_out_of_scope.status_code, 404)
        # Verify SQL queries included project scope
        executed_sqls = [call[0][0] for call in mock_conn.execute.call_args_list]
        self.assertTrue(
            all(
                "project_id = ANY(%s)" in sql and "(%s AND project_id IS NULL)" in sql
                for sql in executed_sqls
            )
        )
        # Verify parameters passed allowed_project_ids list and allow_global_read
        executed_params = [call[0][1] for call in mock_conn.execute.call_args_list]
        self.assertEqual(executed_params[0], (999999, [2], False))
        self.assertEqual(executed_params[1], (1, [2], False))

    def test_get_document_authorized_user_success_and_normal_404(self):
        """Authorized caller gets 200 on existing document and normal 404 on nonexistent document."""
        context = AuthorizationContext(
            identity="authorized-user",
            allowed_project_ids=frozenset({1}),
            allow_global_read=False,
            allow_write=False,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
            patch("app.routers.documents.fetch_document") as mock_fetch,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            # Existing document in project 1
            mock_conn.execute.return_value.fetchone.return_value = (1,)
            mock_fetch.return_value = {"id": 1, "project_id": 1, "title": "Doc 1"}

            client = self._client_with_context(context)
            with client:
                res_existing = client.get("/documents/1")

            # Nonexistent document in project 1
            mock_conn.execute.return_value.fetchone.return_value = None
            with client:
                res_missing = client.get("/documents/999")

        self.assertEqual(res_existing.status_code, 200)
        self.assertEqual(res_existing.json()["id"], 1)
        self.assertEqual(res_missing.status_code, 404)

    def test_get_document_global_read_static_key_shape_prevents_oracle(self):
        """Global-read caller with empty allowed_project_ids (static API key shape)
        queries with SQL scope and gets 404 for both nonexistent and other-project IDs."""
        context = AuthorizationContext(
            identity="static-key-user",
            allowed_project_ids=frozenset(),
            allow_global_read=True,
            allow_write=False,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = None

            client = self._client_with_context(context)
            with client:
                res_missing = client.get("/documents/999999")
                res_other_project = client.get("/documents/1")

        # 1. Nonexistent ID -> 404
        self.assertEqual(res_missing.status_code, 404)
        # 2. Existing ID in another project -> 404
        self.assertEqual(res_other_project.status_code, 404)
        # 3. DB lookup passed correct SQL scope and parameters
        executed_sqls = [call[0][0] for call in mock_conn.execute.call_args_list]
        self.assertTrue(
            all(
                "project_id = ANY(%s)" in sql and "(%s AND project_id IS NULL)" in sql
                for sql in executed_sqls
            )
        )
        executed_params = [call[0][1] for call in mock_conn.execute.call_args_list]
        self.assertEqual(executed_params[0], (999999, [], True))
        self.assertEqual(executed_params[1], (1, [], True))

    # =========================================================================
    # 2. GET /documents/{document_id}/chunks
    # =========================================================================

    def test_get_document_chunks_unauthenticated_prevents_db_lookup_and_returns_403(self):
        """Unauthenticated GET /documents/{id}/chunks returns 403 without querying DB."""
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            res_missing = self.client_no_auth.get("/documents/999999/chunks")
            res_existing = self.client_no_auth.get("/documents/1/chunks")

        self.assertEqual(res_missing.status_code, 403)
        self.assertEqual(res_existing.status_code, 403)
        mock_db.assert_not_called()

    def test_get_document_chunks_scoped_user_applies_sql_filter_and_normalizes_to_404(self):
        """Scoped caller queries chunks with SQL project filter; nonexistent and out-of-scope IDs return 404."""
        context = AuthorizationContext(
            identity="scoped-user",
            allowed_project_ids=frozenset({2}),
            allow_global_read=False,
            allow_write=False,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = None

            client = self._client_with_context(context)
            with client:
                res_missing = client.get("/documents/999999/chunks")
                res_out_of_scope = client.get("/documents/1/chunks")

        self.assertEqual(res_missing.status_code, 404)
        self.assertEqual(res_out_of_scope.status_code, 404)
        executed_sqls = [call[0][0] for call in mock_conn.execute.call_args_list]
        self.assertTrue(
            all(
                "project_id = ANY(%s)" in sql and "(%s AND project_id IS NULL)" in sql
                for sql in executed_sqls
            )
        )
        executed_params = [call[0][1] for call in mock_conn.execute.call_args_list]
        self.assertEqual(executed_params[0], (999999, [2], False))
        self.assertEqual(executed_params[1], (1, [2], False))

    def test_get_document_chunks_authorized_user_success_and_normal_404(self):
        """Authorized caller gets 200 on existing document chunks and normal 404 on nonexistent."""
        context = AuthorizationContext(
            identity="authorized-user",
            allowed_project_ids=frozenset({1}),
            allow_global_read=False,
            allow_write=False,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = (1, 1)
            mock_conn.execute.return_value.fetchall.return_value = [
                (1, 1, 0, "Chunk content", 1, "now", True)
            ]

            client = self._client_with_context(context)
            with client:
                res_existing = client.get("/documents/1/chunks")

            mock_conn.execute.return_value.fetchone.return_value = None
            with client:
                res_missing = client.get("/documents/999/chunks")

        self.assertEqual(res_existing.status_code, 200)
        self.assertEqual(res_existing.json()["count"], 1)
        self.assertEqual(res_missing.status_code, 404)

    def test_get_document_chunks_global_read_static_key_shape_prevents_oracle(self):
        """Global-read caller with empty allowed_project_ids (static API key shape)
        queries chunks with SQL scope and gets 404 for both nonexistent and other-project IDs."""
        context = AuthorizationContext(
            identity="static-key-user",
            allowed_project_ids=frozenset(),
            allow_global_read=True,
            allow_write=False,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = None

            client = self._client_with_context(context)
            with client:
                res_missing = client.get("/documents/999999/chunks")
                res_other_project = client.get("/documents/1/chunks")

        # 1. Nonexistent ID -> 404
        self.assertEqual(res_missing.status_code, 404)
        # 2. Existing ID in another project -> 404
        self.assertEqual(res_other_project.status_code, 404)
        # 3. DB lookup passed correct SQL scope and parameters
        executed_sqls = [call[0][0] for call in mock_conn.execute.call_args_list]
        self.assertTrue(
            all(
                "project_id = ANY(%s)" in sql and "(%s AND project_id IS NULL)" in sql
                for sql in executed_sqls
            )
        )
        executed_params = [call[0][1] for call in mock_conn.execute.call_args_list]
        self.assertEqual(executed_params[0], (999999, [], True))
        self.assertEqual(executed_params[1], (1, [], True))

    # =========================================================================
    # 3. DELETE /documents/{document_id}
    # =========================================================================

    def test_delete_document_unauthenticated_prevents_db_lookup_and_returns_403(self):
        """Unauthenticated DELETE /documents/{id} returns 403 without querying DB."""
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            res_missing = self.client_no_auth.delete("/documents/999999")
            res_existing = self.client_no_auth.delete("/documents/1")

        self.assertEqual(res_missing.status_code, 403)
        self.assertEqual(res_existing.status_code, 403)
        mock_db.assert_not_called()

    def test_delete_document_read_only_caller_denied_before_db_lookup(self):
        """Caller with allow_write=False is denied before DB connection."""
        context = AuthorizationContext(
            identity="reader",
            allowed_project_ids=frozenset({1}),
            allow_global_read=True,
            allow_write=False,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            client = self._client_with_context(context)
            with client:
                response = client.delete("/documents/1")

        self.assertEqual(response.status_code, 403)
        self.assertIn("not authorized for durable writes", response.json()["detail"])
        mock_db.assert_not_called()

    def test_delete_document_scoped_user_applies_sql_filter_and_normalizes_to_404(self):
        """Scoped caller queries with SQL project filter; nonexistent and out-of-scope IDs return 404."""
        context = AuthorizationContext(
            identity="scoped-writer",
            allowed_project_ids=frozenset({2}),
            allow_global_read=False,
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = None

            client = self._client_with_context(context)
            with client:
                res_missing = client.delete("/documents/999999")
                res_out_of_scope = client.delete("/documents/1")

        self.assertEqual(res_missing.status_code, 404)
        self.assertEqual(res_out_of_scope.status_code, 404)
        executed_sqls = [call[0][0] for call in mock_conn.execute.call_args_list]
        self.assertTrue(all("project_id = ANY(%s)" in sql for sql in executed_sqls))
        executed_params = [call[0][1] for call in mock_conn.execute.call_args_list]
        self.assertEqual(executed_params[0], (999999, [2]))
        self.assertEqual(executed_params[1], (1, [2]))

    def test_delete_document_authorized_user_success_and_normal_404(self):
        """Authorized caller deletes existing document (200) and gets normal 404 on nonexistent."""
        context = AuthorizationContext(
            identity="authorized-writer",
            allowed_project_ids=frozenset({1}),
            allow_global_read=False,
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            # First fetchone: (project_id, title), second fetchone: (id, title)
            mock_conn.execute.return_value.fetchone.side_effect = [
                (1, "Doc to delete"),
                (1, "Doc to delete"),
            ]

            client = self._client_with_context(context)
            with client:
                res_existing = client.delete("/documents/1")

            mock_conn.execute.return_value.fetchone.side_effect = None
            mock_conn.execute.return_value.fetchone.return_value = None
            with client:
                res_missing = client.delete("/documents/999")

        self.assertEqual(res_existing.status_code, 200)
        self.assertEqual(res_existing.json()["status"], "deleted")
        self.assertEqual(res_missing.status_code, 404)

    # =========================================================================
    # 4. GET /memories/{memory_id}
    # =========================================================================

    def test_get_memory_unauthenticated_prevents_db_lookup_and_returns_403(self):
        """Unauthenticated GET /memories/{id} returns 403 without querying DB."""
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.memories.get_db_connection") as mock_db,
        ):
            res_missing = self.client_no_auth.get("/memories/999999")
            res_existing = self.client_no_auth.get("/memories/1")

        self.assertEqual(res_missing.status_code, 403)
        self.assertEqual(res_existing.status_code, 403)
        mock_db.assert_not_called()

    def test_get_memory_scoped_user_applies_sql_filter_and_normalizes_to_404(self):
        """Scoped caller queries memory with SQL project filter; nonexistent and out-of-scope IDs return 404."""
        context = AuthorizationContext(
            identity="scoped-user",
            allowed_project_ids=frozenset({2}),
            allow_global_read=False,
            allow_write=False,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.memories.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = None

            client = self._client_with_context(context)
            with client:
                res_missing = client.get("/memories/999999")
                res_out_of_scope = client.get("/memories/1")

        self.assertEqual(res_missing.status_code, 404)
        self.assertEqual(res_out_of_scope.status_code, 404)
        executed_sqls = [call[0][0] for call in mock_conn.execute.call_args_list]
        self.assertTrue(
            all(
                "project_id = ANY(%s)" in sql and "(%s AND project_id IS NULL)" in sql
                for sql in executed_sqls
            )
        )
        executed_params = [call[0][1] for call in mock_conn.execute.call_args_list]
        self.assertEqual(executed_params[0], (999999, [2], False))
        self.assertEqual(executed_params[1], (1, [2], False))

    def test_get_memory_authorized_user_success_and_normal_404(self):
        """Authorized caller gets 200 on existing memory and normal 404 on nonexistent."""
        context = AuthorizationContext(
            identity="authorized-user",
            allowed_project_ids=frozenset({1}),
            allow_global_read=False,
            allow_write=False,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.memories.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.side_effect = [
                (1,),  # project_id check
                (1, "Memory text", "fact", "cat", 3, "api", 1, "now", "now", True),  # full detail
            ]

            client = self._client_with_context(context)
            with client:
                res_existing = client.get("/memories/1")

            mock_conn.execute.return_value.fetchone.side_effect = None
            mock_conn.execute.return_value.fetchone.return_value = None
            with client:
                res_missing = client.get("/memories/999")

        self.assertEqual(res_existing.status_code, 200)
        self.assertEqual(res_existing.json()["id"], 1)
        self.assertEqual(res_missing.status_code, 404)

    def test_get_memory_global_read_static_key_shape_prevents_oracle(self):
        """Global-read caller with empty allowed_project_ids (static API key shape)
        queries memory with SQL scope and gets 404 for both nonexistent and other-project IDs."""
        context = AuthorizationContext(
            identity="static-key-user",
            allowed_project_ids=frozenset(),
            allow_global_read=True,
            allow_write=False,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.memories.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = None

            client = self._client_with_context(context)
            with client:
                res_missing = client.get("/memories/999999")
                res_other_project = client.get("/memories/1")

        # 1. Nonexistent ID -> 404
        self.assertEqual(res_missing.status_code, 404)
        # 2. Existing ID in another project -> 404
        self.assertEqual(res_other_project.status_code, 404)
        # 3. DB lookup passed correct SQL scope and parameters
        executed_sqls = [call[0][0] for call in mock_conn.execute.call_args_list]
        self.assertTrue(
            all(
                "project_id = ANY(%s)" in sql and "(%s AND project_id IS NULL)" in sql
                for sql in executed_sqls
            )
        )
        executed_params = [call[0][1] for call in mock_conn.execute.call_args_list]
        self.assertEqual(executed_params[0], (999999, [], True))
        self.assertEqual(executed_params[1], (1, [], True))

    # =========================================================================
    # 5. DELETE /memories/{memory_id}
    # =========================================================================

    def test_delete_memory_unauthenticated_prevents_db_lookup_and_returns_403(self):
        """Unauthenticated DELETE /memories/{id} returns 403 without querying DB."""
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.memories.get_db_connection") as mock_db,
        ):
            res_missing = self.client_no_auth.delete("/memories/999999")
            res_existing = self.client_no_auth.delete("/memories/1")

        self.assertEqual(res_missing.status_code, 403)
        self.assertEqual(res_existing.status_code, 403)
        mock_db.assert_not_called()

    def test_delete_memory_read_only_caller_denied_before_db_lookup(self):
        """Caller with allow_write=False is denied before DB connection."""
        context = AuthorizationContext(
            identity="reader",
            allowed_project_ids=frozenset({1}),
            allow_global_read=True,
            allow_write=False,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.memories.get_db_connection") as mock_db,
        ):
            client = self._client_with_context(context)
            with client:
                response = client.delete("/memories/1")

        self.assertEqual(response.status_code, 403)
        self.assertIn("not authorized for durable writes", response.json()["detail"])
        mock_db.assert_not_called()

    def test_delete_memory_scoped_user_applies_sql_filter_and_normalizes_to_404(self):
        """Scoped caller queries with SQL project filter; nonexistent and out-of-scope IDs return 404."""
        context = AuthorizationContext(
            identity="scoped-writer",
            allowed_project_ids=frozenset({2}),
            allow_global_read=False,
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.memories.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = None

            client = self._client_with_context(context)
            with client:
                res_missing = client.delete("/memories/999999")
                res_out_of_scope = client.delete("/memories/1")

        self.assertEqual(res_missing.status_code, 404)
        self.assertEqual(res_out_of_scope.status_code, 404)
        executed_sqls = [call[0][0] for call in mock_conn.execute.call_args_list]
        self.assertTrue(all("project_id = ANY(%s)" in sql for sql in executed_sqls))
        executed_params = [call[0][1] for call in mock_conn.execute.call_args_list]
        self.assertEqual(executed_params[0], (999999, [2]))
        self.assertEqual(executed_params[1], (1, [2]))

    def test_delete_memory_authorized_user_success_and_normal_404(self):
        """Authorized caller deletes existing memory (200) and gets normal 404 on nonexistent."""
        context = AuthorizationContext(
            identity="authorized-writer",
            allowed_project_ids=frozenset({1}),
            allow_global_read=False,
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.memories.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.side_effect = [
                (1,),  # project_id check
                (1,),  # DELETE RETURNING id
            ]

            client = self._client_with_context(context)
            with client:
                res_existing = client.delete("/memories/1")

            mock_conn.execute.return_value.fetchone.side_effect = None
            mock_conn.execute.return_value.fetchone.return_value = None
            with client:
                res_missing = client.delete("/memories/999")

        self.assertEqual(res_existing.status_code, 200)
        self.assertEqual(res_existing.json()["status"], "deleted")
        self.assertEqual(res_missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
