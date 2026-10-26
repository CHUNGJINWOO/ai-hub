"""
Tests for Static API Key RBAC and Authorization Invariants.

Covers:
- get_static_key_context() configuration parsing and Fail-Closed defaults
- Invariant: write_project_ids ⊆ read_project_ids
- verify_rest_token() with constant-time HMAC comparison
- REST API E2E authorization via TestClient (200 vs 403, existence masking 404)
- MCP KeycloakJWTVerifier static key claim population and tool authorization
- Non-interference with Keycloak JWT authorization path
"""

import hmac
import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.core.authorization import (
    AuthorizationContext,
    AuthorizationDenied,
    ProjectScope,
    authorize_read,
    authorize_write,
    mcp_authorization_context,
    require_mcp_project_access,
)
from app.core.capabilities import Capability
from app.core.token_verifier import (
    authorization_context_from_claims,
    get_static_key_context,
    verify_rest_token,
)
from app.main import app
from app.mcp_server import (
    KeycloakJWTVerifier,
    _authorization_context_from_access_token,
)

_TEST_STATIC_KEY = "test-static-api-token-secret"


class StaticKeyContextConfigTests(unittest.TestCase):
    """Unit tests for get_static_key_context() configuration and defaults."""

    def test_default_fail_closed_configuration(self):
        """When no env vars are set, fail-closed defaults apply."""
        with patch.dict(os.environ, {}, clear=True):
            ctx = get_static_key_context()
            self.assertEqual(ctx.identity, "service:static-api-key")
            self.assertEqual(ctx.read_project_ids, frozenset())
            self.assertEqual(ctx.write_project_ids, frozenset())
            self.assertEqual(ctx.capabilities, frozenset({Capability.RESOURCE_READ_GLOBAL}))
            # Verify authorization predicates
            self.assertTrue(ctx.can_read_global())
            self.assertFalse(ctx.can_write_global())
            self.assertFalse(ctx.can_read_project(1))
            self.assertFalse(ctx.can_read_project(2))
            self.assertFalse(ctx.can_write_project(1))
            self.assertFalse(ctx.can_create_project())

    def test_custom_service_identity(self):
        """Custom identity string is respected; empty/blank falls back to default."""
        with patch.dict(
            os.environ,
            {"AIHUB_STATIC_KEY_IDENTITY": "service:oracle-mcp-agent"},
            clear=True,
        ):
            ctx = get_static_key_context()
            self.assertEqual(ctx.identity, "service:oracle-mcp-agent")

        with patch.dict(
            os.environ,
            {"AIHUB_STATIC_KEY_IDENTITY": "   "},
            clear=True,
        ):
            ctx = get_static_key_context()
            self.assertEqual(ctx.identity, "service:static-api-key")

    def test_capabilities_configuration(self):
        """AIHUB_STATIC_KEY_CAPABILITIES grants specific capabilities."""
        with patch.dict(
            os.environ,
            {
                "AIHUB_STATIC_KEY_CAPABILITIES": (
                    "PROJECT_READ_ALL, RESOURCE_READ_GLOBAL, PROJECT_CREATE"
                )
            },
            clear=True,
        ):
            ctx = get_static_key_context()
            self.assertIn(Capability.PROJECT_READ_ALL, ctx.capabilities)
            self.assertIn(Capability.RESOURCE_READ_GLOBAL, ctx.capabilities)
            self.assertIn(Capability.PROJECT_CREATE, ctx.capabilities)
            self.assertTrue(ctx.can_read_project(1))
            self.assertTrue(ctx.can_read_project(999))
            self.assertTrue(ctx.can_create_project())

    def test_scoped_read_projects(self):
        """AIHUB_STATIC_KEY_READ_PROJECTS restricts read access to specified IDs."""
        with patch.dict(
            os.environ,
            {"AIHUB_STATIC_KEY_READ_PROJECTS": "1, 2, 42"},
            clear=True,
        ):
            ctx = get_static_key_context()
            self.assertEqual(ctx.read_project_ids, frozenset({1, 2, 42}))
            self.assertTrue(ctx.can_read_project(1))
            self.assertTrue(ctx.can_read_project(2))
            self.assertTrue(ctx.can_read_project(42))
            self.assertFalse(ctx.can_read_project(3))
            self.assertFalse(ctx.can_read_project(99))

    def test_write_subset_of_read_invariant_enforced(self):
        """Write project IDs must be a subset of read project IDs."""
        with patch.dict(
            os.environ,
            {
                "AIHUB_STATIC_KEY_READ_PROJECTS": "1",
                "AIHUB_STATIC_KEY_WRITE_PROJECTS": "2",
            },
            clear=True,
        ):
            ctx = get_static_key_context()
            self.assertIn(2, ctx.write_project_ids)
            # 2 must automatically be included in read_project_ids
            self.assertTrue(ctx.write_project_ids.issubset(ctx.read_project_ids))
            self.assertEqual(ctx.read_project_ids, frozenset({1, 2}))
            self.assertTrue(ctx.can_read_project(2))
            self.assertTrue(ctx.can_write_project(2))

    def test_malformed_env_values_handled_gracefully(self):
        """Malformed integers and unknown capability strings are safely ignored."""
        with patch.dict(
            os.environ,
            {
                "AIHUB_STATIC_KEY_READ_PROJECTS": "1, invalid, , 3, 100",
                "AIHUB_STATIC_KEY_WRITE_PROJECTS": "not_a_num, 3",
                "AIHUB_STATIC_KEY_CAPABILITIES": "NON_EXISTENT_CAP, RESOURCE_READ_GLOBAL",
            },
            clear=True,
        ):
            ctx = get_static_key_context()
            self.assertEqual(ctx.read_project_ids, frozenset({1, 3, 100}))
            self.assertEqual(ctx.write_project_ids, frozenset({3}))
            self.assertEqual(ctx.capabilities, frozenset({Capability.RESOURCE_READ_GLOBAL}))

    def test_duplicate_project_ids_deduplicated(self):
        """Duplicate project IDs in comma-separated list are cleanly deduplicated."""
        with patch.dict(
            os.environ,
            {
                "AIHUB_STATIC_KEY_READ_PROJECTS": "1, 1, 2, 2, 2, 3",
                "AIHUB_STATIC_KEY_WRITE_PROJECTS": "2, 2",
            },
            clear=True,
        ):
            ctx = get_static_key_context()
            self.assertEqual(ctx.read_project_ids, frozenset({1, 2, 3}))
            self.assertEqual(ctx.write_project_ids, frozenset({2}))

    def test_empty_env_strings_remain_fail_closed(self):
        """Explicitly empty strings in env vars result in strict Fail-Closed defaults."""
        with patch.dict(
            os.environ,
            {
                "AIHUB_STATIC_KEY_IDENTITY": "",
                "AIHUB_STATIC_KEY_READ_PROJECTS": "",
                "AIHUB_STATIC_KEY_WRITE_PROJECTS": "",
                "AIHUB_STATIC_KEY_CAPABILITIES": "",
            },
            clear=True,
        ):
            ctx = get_static_key_context()
            self.assertEqual(ctx.identity, "service:static-api-key")
            self.assertEqual(ctx.read_project_ids, frozenset())
            self.assertEqual(ctx.write_project_ids, frozenset())
            self.assertEqual(ctx.capabilities, frozenset({Capability.RESOURCE_READ_GLOBAL}))
            self.assertFalse(ctx.can_read_project(1))
            self.assertFalse(ctx.can_write_project(1))

    def test_unknown_capabilities_do_not_escalate_privilege(self):
        """Invalid or attacker-supplied capability names never grant elevated privileges."""
        with patch.dict(
            os.environ,
            {
                "AIHUB_STATIC_KEY_CAPABILITIES": "ADMIN, ROOT, SUPERUSER, *, ALL, PROJECT_ADMIN_ALL",
            },
            clear=True,
        ):
            ctx = get_static_key_context()
            # None of the unknown strings match Capability values; capabilities set remains empty
            self.assertEqual(ctx.capabilities, frozenset())
            self.assertFalse(ctx.can_read_project(1))
            self.assertFalse(ctx.can_write_project(1))
            self.assertFalse(ctx.can_create_project())
            self.assertFalse(ctx.can_read_global())
            self.assertFalse(ctx.can_write_global())



class VerifyRestTokenStaticKeyTests(unittest.TestCase):
    """Unit tests for verify_rest_token() static key verification."""

    def test_verify_valid_token_matches(self):
        ctx = verify_rest_token(_TEST_STATIC_KEY, api_key=_TEST_STATIC_KEY)
        self.assertIsNotNone(ctx)
        self.assertIsInstance(ctx, AuthorizationContext)

    def test_verify_invalid_token_returns_none(self):
        ctx = verify_rest_token("wrong-token", api_key=_TEST_STATIC_KEY)
        self.assertIsNone(ctx)

    def test_verify_empty_token_returns_none(self):
        ctx = verify_rest_token("", api_key=_TEST_STATIC_KEY)
        self.assertIsNone(ctx)

    def test_verify_none_api_key_skips_static(self):
        ctx = verify_rest_token(_TEST_STATIC_KEY, api_key=None)
        self.assertIsNone(ctx)


class RestAuthorizationE2ETests(unittest.TestCase):
    """E2E REST API tests using TestClient with real Bearer static key."""

    def setUp(self):
        self.client = TestClient(app)

    def test_get_documents_with_project_read_all(self):
        """When PROJECT_READ_ALL is enabled, queries with project_id succeed."""
        with patch.dict(
            os.environ,
            {
                "AIHUB_STATIC_KEY_CAPABILITIES": "PROJECT_READ_ALL,RESOURCE_READ_GLOBAL",
                "MCP_ACCESS_TOKEN": _TEST_STATIC_KEY,
            },
        ):
            with patch("app.main._REST_API_KEY", _TEST_STATIC_KEY):
                with patch("app.routers.documents.get_db_connection") as mock_conn_fn:
                    mock_conn = MagicMock()
                    mock_conn_fn.return_value.__enter__.return_value = mock_conn
                    mock_conn.execute.return_value.fetchall.return_value = []

                    response = self.client.get(
                        "/documents",
                        params={"project_id": 2},
                        headers={"Authorization": f"Bearer {_TEST_STATIC_KEY}"},
                    )
                    self.assertEqual(response.status_code, 200)

    def test_get_documents_with_scoped_project_allows_allowed_and_denies_unallowed(self):
        """When AIHUB_STATIC_KEY_READ_PROJECTS='2', project 2 succeeds and project 1 returns 403."""
        with patch.dict(
            os.environ,
            {
                "AIHUB_STATIC_KEY_READ_PROJECTS": "2",
                "AIHUB_STATIC_KEY_CAPABILITIES": "RESOURCE_READ_GLOBAL",
                "MCP_ACCESS_TOKEN": _TEST_STATIC_KEY,
            },
        ):
            with patch("app.main._REST_API_KEY", _TEST_STATIC_KEY):
                with patch("app.routers.documents.get_db_connection") as mock_conn_fn:
                    mock_conn = MagicMock()
                    mock_conn_fn.return_value.__enter__.return_value = mock_conn
                    mock_conn.execute.return_value.fetchall.return_value = []

                    # Project 2 is allowed
                    resp_allowed = self.client.get(
                        "/documents",
                        params={"project_id": 2},
                        headers={"Authorization": f"Bearer {_TEST_STATIC_KEY}"},
                    )
                    self.assertEqual(resp_allowed.status_code, 200)

                    # Project 1 is NOT allowed -> 403 Forbidden
                    resp_denied = self.client.get(
                        "/documents",
                        params={"project_id": 1},
                        headers={"Authorization": f"Bearer {_TEST_STATIC_KEY}"},
                    )
                    self.assertEqual(resp_denied.status_code, 403)
                    self.assertIn("not authorized for project: 1", resp_denied.json()["detail"])

    def test_get_document_by_id_existence_masking(self):
        """Document in unallowed project returns 404 (existence masking), not 403."""
        with patch.dict(
            os.environ,
            {
                "AIHUB_STATIC_KEY_READ_PROJECTS": "2",
                "AIHUB_STATIC_KEY_CAPABILITIES": "RESOURCE_READ_GLOBAL",
                "MCP_ACCESS_TOKEN": _TEST_STATIC_KEY,
            },
        ):
            with patch("app.main._REST_API_KEY", _TEST_STATIC_KEY):
                with patch("app.routers.documents.get_db_connection") as mock_conn_fn:
                    mock_conn = MagicMock()
                    mock_conn_fn.return_value.__enter__.return_value = mock_conn

                    # Simulate DB query: returns None because document project (1) != context read_projects ({2})
                    mock_conn.execute.return_value.fetchone.return_value = None

                    resp = self.client.get(
                        "/documents/1",
                        headers={"Authorization": f"Bearer {_TEST_STATIC_KEY}"},
                    )
                    self.assertEqual(resp.status_code, 404)
                    self.assertEqual(resp.json()["detail"], "Document not found")

    def test_request_with_invalid_token_returns_403(self):
        """Invalid Bearer token leaves context unset, causing route guards to return 403."""
        with patch("app.main._REST_API_KEY", _TEST_STATIC_KEY):
            resp = self.client.get(
                "/documents",
                params={"project_id": 2},
                headers={"Authorization": "Bearer wrong-token"},
            )
            self.assertEqual(resp.status_code, 403)

    def test_request_without_token_returns_403(self):
        """Missing Authorization header leaves context unset, causing route guards to return 403."""
        resp = self.client.get(
            "/documents",
            params={"project_id": 2},
        )
        self.assertEqual(resp.status_code, 403)


class MCPAuthorizationE2ETests(unittest.IsolatedAsyncioTestCase):
    """MCP TokenVerifier and Tool execution authorization tests."""

    async def test_mcp_static_token_claims_population(self):
        """MCP KeycloakJWTVerifier populates read_project_ids and capabilities in claims."""
        with patch.dict(
            os.environ,
            {
                "AIHUB_STATIC_KEY_IDENTITY": "service:mcp-test-runner",
                "AIHUB_STATIC_KEY_READ_PROJECTS": "2, 3",
                "AIHUB_STATIC_KEY_WRITE_PROJECTS": "2",
                "AIHUB_STATIC_KEY_CAPABILITIES": "RESOURCE_READ_GLOBAL,PROJECT_READ_ALL",
            },
        ):
            verifier = KeycloakJWTVerifier(
                jwks_url="http://keycloak:8080/certs",
                issuer="https://keycloak/issuer",
                audience="https://ros2-server:10000/mcp",
                api_key=_TEST_STATIC_KEY,
            )

            access_token = await verifier.verify_token(_TEST_STATIC_KEY)
            self.assertIsNotNone(access_token)
            self.assertEqual(access_token.client_id, "service:mcp-test-runner")
            self.assertEqual(access_token.subject, "service:mcp-test-runner")
            self.assertIn("read_project_ids", access_token.claims)
            self.assertEqual(set(access_token.claims["read_project_ids"]), {2, 3})
            self.assertEqual(set(access_token.claims["write_project_ids"]), {2})
            self.assertIn("PROJECT_READ_ALL", access_token.claims["capabilities"])

            # Verify translation to AuthorizationContext
            ctx = _authorization_context_from_access_token(access_token)
            self.assertEqual(ctx.identity, "service:mcp-test-runner")
            self.assertEqual(ctx.read_project_ids, frozenset({2, 3}))
            self.assertEqual(ctx.write_project_ids, frozenset({2}))
            self.assertTrue(ctx.can_read_project(2))
            self.assertTrue(ctx.can_read_project(3))
            self.assertTrue(ctx.can_write_project(2))
            self.assertFalse(ctx.can_write_project(3))

    async def test_mcp_invalid_token_returns_none(self):
        verifier = KeycloakJWTVerifier(
            jwks_url="http://keycloak:8080/certs",
            issuer="https://keycloak/issuer",
            audience="https://ros2-server:10000/mcp",
            api_key=_TEST_STATIC_KEY,
        )
        access_token = await verifier.verify_token("invalid-mcp-key")
        self.assertIsNone(access_token)

    def test_mcp_tool_guard_enforcement(self):
        """require_mcp_project_access enforces context permissions."""
        ctx = AuthorizationContext(
            identity="mcp-client",
            read_project_ids=frozenset({2}),
            write_project_ids=frozenset(),
            capabilities=frozenset({Capability.RESOURCE_READ_GLOBAL}),
        )

        with mcp_authorization_context(ctx):
            # Allowed project succeeds without error
            require_mcp_project_access(operation="read", project_id=2)

            # Unallowed project raises AuthorizationDenied
            with self.assertRaises(AuthorizationDenied):
                require_mcp_project_access(operation="read", project_id=1)


class JWTCompatibilityTests(unittest.TestCase):
    """Verify that JWT verification path is unaffected by static key features."""

    def test_jwt_path_delegates_to_jwks_when_token_is_not_static_key(self):

        # Use patch to verify verify_rest_token invokes get_signing_key_from_jwt for non-static token
        with patch("app.core.token_verifier._jwks_client") as mock_client:
            mock_key = MagicMock()
            mock_key.key = "public_key"
            mock_client.get_signing_key_from_jwt.return_value = mock_key

            with patch("app.core.token_verifier.jwt.decode") as mock_decode:
                mock_decode.return_value = {
                    "sub": "user-uuid-1234",
                    "scope": "aihub:read",
                    "aud": "https://ros2-server.tail49948f.ts.net:10000/mcp",
                    "read_project_ids": [10, 20],
                }

                ctx = verify_rest_token("jwt.header.payload", api_key=_TEST_STATIC_KEY)
                self.assertIsNotNone(ctx)
                self.assertEqual(ctx.identity, "user-uuid-1234")
                self.assertEqual(ctx.read_project_ids, frozenset({10, 20}))
                mock_client.get_signing_key_from_jwt.assert_called_once_with("jwt.header.payload")
