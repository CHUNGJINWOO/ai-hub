import unittest

from fastapi import Request

from app.core.authorization import (
    AuthorizationContext,
    AuthorizationDenied,
    mcp_authorization_context,
    require_mcp_project_access,
    require_project_access,
    require_request_project_access,
)


class AuthorizationGuardTests(unittest.TestCase):
    def setUp(self):
        self.context = AuthorizationContext(
            identity="agent-1",
            allowed_project_ids=frozenset({7}),
            allow_write=True,
        )

    def test_allows_read_for_an_authorized_project(self):
        require_project_access(
            self.context,
            operation="read",
            project_id=7,
        )

    def test_denies_read_for_an_unauthorized_project(self):
        with self.assertRaisesRegex(AuthorizationDenied, "project: 8"):
            require_project_access(
                self.context,
                operation="read",
                project_id=8,
            )

    def test_denies_projectless_read_without_global_permission(self):
        with self.assertRaisesRegex(AuthorizationDenied, "project_id is required"):
            require_project_access(
                self.context,
                operation="read",
                project_id=None,
            )

    def test_allows_explicit_global_read(self):
        context = AuthorizationContext(
            identity="reader",
            allowed_project_ids=frozenset(),
            allow_global_read=True,
        )

        require_project_access(
            context,
            operation="read",
            project_id=None,
        )

    def test_requires_project_for_durable_write(self):
        with self.assertRaisesRegex(AuthorizationDenied, "project_id is required"):
            require_project_access(
                self.context,
                operation="write",
                project_id=None,
            )

    def test_requires_write_permission(self):
        context = AuthorizationContext(
            identity="reader",
            allowed_project_ids=frozenset({7}),
        )

        with self.assertRaisesRegex(AuthorizationDenied, "durable writes"):
            require_project_access(
                context,
                operation="write",
                project_id=7,
            )

    def test_request_boundary_requires_external_context(self):
        scope = {"type": "http", "state": {}}
        request = Request(scope)

        with self.assertRaisesRegex(
            AuthorizationDenied,
            "context is not available",
        ):
            require_request_project_access(
                request,
                operation="read",
                project_id=7,
            )

    def test_mcp_boundary_uses_supplied_context(self):
        with mcp_authorization_context(self.context):
            require_mcp_project_access(
                operation="read",
                project_id=7,
            )

    def test_mcp_boundary_fails_closed_without_context(self):
        with self.assertRaisesRegex(
            AuthorizationDenied,
            "context is not available",
        ):
            require_mcp_project_access(
                operation="read",
                project_id=7,
            )


if __name__ == "__main__":
    unittest.main()
