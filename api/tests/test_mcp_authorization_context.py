import asyncio
import os
import unittest
from unittest.mock import patch

from mcp.server.auth.provider import AccessToken

os.environ.setdefault("MCP_ACCESS_TOKEN", "test-only-mcp-token")

from app.core.authorization import (
    AuthorizationContext,
    AuthorizationDenied,
    _mcp_authorization_context,
    require_mcp_project_access,
)
from app.mcp_server import (
    MCPAuthorizationContextMiddleware,
    _authorization_context_from_access_token,
)


class McpAuthorizationContextTests(unittest.TestCase):
    def test_static_api_key_maps_to_global_read_context(self):
        context = _authorization_context_from_access_token(
            AccessToken(
                token="redacted",
                client_id="static-api-key",
                scopes=["aihub:read"],
                resource="http://localhost/mcp",
                subject="static-api-key",
                claims={"auth_method": "static_api_key"},
            )
        )

        self.assertEqual(
            context,
            AuthorizationContext(
                identity="static-api-key",
                allowed_project_ids=frozenset(),
                allow_global_read=True,
            ),
        )

    def test_claims_map_project_and_global_permissions(self):
        context = _authorization_context_from_access_token(
            AccessToken(
                token="redacted",
                client_id="client",
                scopes=["aihub:read"],
                resource="http://localhost/mcp",
                subject="user-7",
                claims={
                    "allowed_project_ids": [7, "8"],
                    "allow_global_read": True,
                    "allow_write": True,
                },
            )
        )

        self.assertEqual(context.identity, "user-7")
        self.assertEqual(
            context.allowed_project_ids,
            frozenset({7, 8}),
        )
        self.assertTrue(context.allow_global_read)
        self.assertTrue(context.allow_write)

    def test_missing_principal_is_denied(self):
        with self.assertRaisesRegex(
            AuthorizationDenied,
            "does not contain a principal",
        ):
            _authorization_context_from_access_token(
                AccessToken(
                    token="redacted",
                    client_id="",
                    scopes=["aihub:read"],
                    claims={},
                )
            )

    def test_invalid_project_permission_claim_is_denied(self):
        invalid_claims = [
            {"allowed_project_ids": "not-a-list"},
            {"allowed_project_ids": ["invalid-id"]},
            {"allowed_project_ids": [None]},
        ]

        for claims in invalid_claims:
            with self.subTest(claims=claims):
                with self.assertRaisesRegex(
                    AuthorizationDenied,
                    "authenticated token contains invalid project permissions",
                ):
                    _authorization_context_from_access_token(
                        AccessToken(
                            token="redacted",
                            client_id="client",
                            scopes=["aihub:read"],
                            subject="user-7",
                            claims=claims,
                        )
                    )

    def test_middleware_injects_context_for_verified_token(self):
        middleware = MCPAuthorizationContextMiddleware()
        observed_context: AuthorizationContext | None = None

        async def call_next(_ctx):
            nonlocal observed_context
            observed_context = _mcp_authorization_context.get()
            return {"ok": True}

        access_token = AccessToken(
            token="redacted",
            client_id="client",
            scopes=["aihub:read"],
            subject="user-7",
            claims={
                "allowed_project_ids": [7],
                "allow_global_read": True,
                "allow_write": False,
            },
        )

        with patch(
            "app.mcp_server.get_access_token",
            return_value=access_token,
        ):
            result = asyncio.run(middleware(object(), call_next))

        self.assertEqual(result, {"ok": True})
        self.assertIsNotNone(observed_context)
        self.assertEqual(observed_context.identity, "user-7")
        self.assertEqual(observed_context.allowed_project_ids, frozenset({7}))
        self.assertTrue(observed_context.allow_global_read)
        self.assertFalse(observed_context.allow_write)
        self.assertIsNone(_mcp_authorization_context.get())

    def test_middleware_passes_through_when_access_token_is_none(self):
        middleware = MCPAuthorizationContextMiddleware()
        observed_context: AuthorizationContext | None = "sentinel"

        async def call_next(_ctx):
            nonlocal observed_context
            observed_context = _mcp_authorization_context.get()
            return {"ok": True}

        with patch(
            "app.mcp_server.get_access_token",
            return_value=None,
        ):
            result = asyncio.run(middleware(object(), call_next))

        self.assertEqual(result, {"ok": True})
        self.assertIsNone(observed_context)
        self.assertIsNone(_mcp_authorization_context.get())

    def test_middleware_preserves_project_only_global_read_denial(self):
        middleware = MCPAuthorizationContextMiddleware()
        access_token = AccessToken(
            token="redacted",
            client_id="client",
            scopes=["aihub:read"],
            subject="user-7",
            claims={"allowed_project_ids": [7]},
        )

        async def call_next(_ctx):
            require_mcp_project_access(
                operation="read",
                project_id=None,
            )
            return {"ok": True}

        with patch(
            "app.mcp_server.get_access_token",
            return_value=access_token,
        ):
            with self.assertRaisesRegex(
                AuthorizationDenied,
                "project_id is required",
            ):
                asyncio.run(middleware(object(), call_next))
