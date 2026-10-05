"""
Tests for /projects router authorization.

Covers requirements:
A. POST /projects
   - no auth -> 403
   - allow_write=False -> 403
   - allow_write=True -> allowed

B. GET /projects
   - no auth -> 403
   - global read -> all projects
   - project-scoped read -> only allowed projects
   - empty allowed_project_ids -> empty result

C. GET /projects/{id}
   - no auth -> 403
   - allowed project -> allowed
   - unauthorized project -> 403
   - global read -> allowed

D. PATCH /projects/{id}
   - no auth -> 403
   - no write permission -> 403
   - unauthorized project -> 403
   - authorized write -> allowed

E. DELETE /projects/{id}
   - same authorization cases as PATCH

F. Authorization ordering
   - unauthorized access denied before DB/resource lookup
   - no information leak through 404/403 differences
"""

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("MCP_ACCESS_TOKEN", "test-only-mcp-token")

from fastapi.testclient import TestClient

from app.core.authorization import AuthorizationContext
from app.main import app, _REST_API_KEY


_TEST_API_KEY: str = _REST_API_KEY or "test-only-mcp-token"


class ProjectsAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def _client_with_context(self, context: AuthorizationContext) -> TestClient:
        client = TestClient(app)
        client.app_state["authorization_context"] = context
        return client

    # ---------------------------------------------------------------------------
    # A. POST /projects
    # ---------------------------------------------------------------------------

    def test_create_project_no_auth_returns_403(self):
        """Unauthenticated project creation is rejected with 403."""
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
        ):
            response = self.client.post(
                "/projects",
                json={"name": "New Project", "slug": "new-proj"},
            )
        self.assertEqual(response.status_code, 403)

    def test_create_project_allow_write_false_returns_403(self):
        """Project creation without allow_write permission is rejected with 403."""
        context = AuthorizationContext(
            identity="reader",
            allowed_project_ids=frozenset({1}),
            allow_global_read=True,
            allow_write=False,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
        ):
            client = self._client_with_context(context)
            with client:
                response = client.post(
                    "/projects",
                    json={"name": "New Project", "slug": "new-proj"},
                )
        self.assertEqual(response.status_code, 403)

    def test_create_project_allow_write_true_allowed(self):
        """Project creation with allow_write=True succeeds."""
        context = AuthorizationContext(
            identity="writer",
            allowed_project_ids=frozenset(),
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.projects.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            # slug uniqueness check returns None
            mock_conn.execute.return_value.fetchone.side_effect = [
                None,  # SELECT id FROM projects WHERE slug = %s
                (1, "New Project", "new-proj", None, "active", "now", "now"),  # INSERT RETURNING
            ]
            client = self._client_with_context(context)
            with client:
                response = client.post(
                    "/projects",
                    json={"name": "New Project", "slug": "new-proj"},
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "New Project")

    # ---------------------------------------------------------------------------
    # B. GET /projects
    # ---------------------------------------------------------------------------

    def test_list_projects_no_auth_returns_403(self):
        """Listing projects without authentication is rejected with 403."""
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
        ):
            response = self.client.get("/projects")
        self.assertEqual(response.status_code, 403)

    def test_list_projects_global_read_returns_all(self):
        """Global read context retrieves all projects without filtering."""
        context = AuthorizationContext(
            identity="global-reader",
            allowed_project_ids=frozenset(),
            allow_global_read=True,
        )
        fake_projects = {
            "count": 2,
            "projects": [
                {"id": 1, "name": "P1", "slug": "p1"},
                {"id": 2, "name": "P2", "slug": "p2"},
            ],
        }
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.projects.fetch_projects") as mock_fetch,
        ):
            mock_fetch.return_value = fake_projects
            client = self._client_with_context(context)
            with client:
                response = client.get("/projects")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 2)
        mock_fetch.assert_called_once_with()

    def test_list_projects_scoped_read_filters_allowed(self):
        """Scoped read context passes allowed_project_ids to fetch_projects."""
        context = AuthorizationContext(
            identity="scoped-reader",
            allowed_project_ids=frozenset({1}),
            allow_global_read=False,
        )
        fake_projects = {
            "count": 1,
            "projects": [{"id": 1, "name": "P1", "slug": "p1"}],
        }
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.projects.fetch_projects") as mock_fetch,
        ):
            mock_fetch.return_value = fake_projects
            client = self._client_with_context(context)
            with client:
                response = client.get("/projects")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        mock_fetch.assert_called_once_with(allowed_project_ids=frozenset({1}))

    def test_list_projects_empty_allowed_returns_empty(self):
        """Empty allowed_project_ids with allow_global_read=False returns empty count."""
        context = AuthorizationContext(
            identity="empty-reader",
            allowed_project_ids=frozenset(),
            allow_global_read=False,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.core.projects.get_db_connection") as mock_db,
        ):
            client = self._client_with_context(context)
            with client:
                response = client.get("/projects")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"count": 0, "projects": []})
        mock_db.assert_not_called()

    # ---------------------------------------------------------------------------
    # C. GET /projects/{id}
    # ---------------------------------------------------------------------------

    def test_get_project_no_auth_returns_403(self):
        """Unauthenticated GET /projects/{id} returns 403."""
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
        ):
            response = self.client.get("/projects/1")
        self.assertEqual(response.status_code, 403)

    def test_get_project_allowed_project_succeeds(self):
        """GET /projects/{id} for authorized project_id returns 200."""
        context = AuthorizationContext(
            identity="user",
            allowed_project_ids=frozenset({1}),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.projects.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = (
                1, "P1", "p1", "desc", "active", "now", "now", 5
            )
            client = self._client_with_context(context)
            with client:
                response = client.get("/projects/1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "P1")

    def test_get_project_unauthorized_project_returns_403(self):
        """GET /projects/{id} for unauthorized project returns 403."""
        context = AuthorizationContext(
            identity="user",
            allowed_project_ids=frozenset({2}),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.projects.get_db_connection") as mock_db,
        ):
            client = self._client_with_context(context)
            with client:
                response = client.get("/projects/1")
        self.assertEqual(response.status_code, 403)
        mock_db.assert_not_called()

    def test_get_project_global_read_allowed(self):
        """GET /projects/{id} with allow_global_read=True permits access to any project."""
        context = AuthorizationContext(
            identity="global-user",
            allowed_project_ids=frozenset(),
            allow_global_read=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.projects.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = (
                1, "P1", "p1", "desc", "active", "now", "now", 5
            )
            client = self._client_with_context(context)
            with client:
                response = client.get("/projects/1")
        self.assertEqual(response.status_code, 200)

    # ---------------------------------------------------------------------------
    # D. PATCH /projects/{id}
    # ---------------------------------------------------------------------------

    def test_patch_project_no_auth_returns_403(self):
        """PATCH /projects/{id} without auth returns 403."""
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
        ):
            response = self.client.patch("/projects/1", json={"name": "Updated"})
        self.assertEqual(response.status_code, 403)

    def test_patch_project_no_write_permission_returns_403(self):
        """PATCH /projects/{id} with allow_write=False returns 403."""
        context = AuthorizationContext(
            identity="reader",
            allowed_project_ids=frozenset({1}),
            allow_global_read=True,
            allow_write=False,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
        ):
            client = self._client_with_context(context)
            with client:
                response = client.patch("/projects/1", json={"name": "Updated"})
        self.assertEqual(response.status_code, 403)

    def test_patch_project_unauthorized_project_returns_403(self):
        """PATCH /projects/{id} where project_id not in allowed_project_ids returns 403."""
        context = AuthorizationContext(
            identity="writer",
            allowed_project_ids=frozenset({2}),
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.projects.get_db_connection") as mock_db,
        ):
            client = self._client_with_context(context)
            with client:
                response = client.patch("/projects/1", json={"name": "Updated"})
        self.assertEqual(response.status_code, 403)
        mock_db.assert_not_called()

    def test_patch_project_authorized_write_succeeds(self):
        """PATCH /projects/{id} with authorized write succeeds."""
        context = AuthorizationContext(
            identity="writer",
            allowed_project_ids=frozenset({1}),
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.projects.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.side_effect = [
                ("P1", "p1", "desc", "active"),  # SELECT current
                None,                            # SELECT existing slug
                (1, "Updated", "p1", "desc", "active", "now", "now"),  # UPDATE RETURNING
            ]
            client = self._client_with_context(context)
            with client:
                response = client.patch("/projects/1", json={"name": "Updated"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "Updated")

    # ---------------------------------------------------------------------------
    # E. DELETE /projects/{id}
    # ---------------------------------------------------------------------------

    def test_delete_project_no_auth_returns_403(self):
        """DELETE /projects/{id} without auth returns 403."""
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
        ):
            response = self.client.delete("/projects/1")
        self.assertEqual(response.status_code, 403)

    def test_delete_project_no_write_permission_returns_403(self):
        """DELETE /projects/{id} without allow_write returns 403."""
        context = AuthorizationContext(
            identity="reader",
            allowed_project_ids=frozenset({1}),
            allow_write=False,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
        ):
            client = self._client_with_context(context)
            with client:
                response = client.delete("/projects/1")
        self.assertEqual(response.status_code, 403)

    def test_delete_project_unauthorized_project_returns_403(self):
        """DELETE /projects/{id} for unauthorized project returns 403."""
        context = AuthorizationContext(
            identity="writer",
            allowed_project_ids=frozenset({2}),
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.projects.get_db_connection") as mock_db,
        ):
            client = self._client_with_context(context)
            with client:
                response = client.delete("/projects/1")
        self.assertEqual(response.status_code, 403)
        mock_db.assert_not_called()

    def test_delete_project_authorized_write_succeeds(self):
        """DELETE /projects/{id} with authorized write succeeds."""
        context = AuthorizationContext(
            identity="writer",
            allowed_project_ids=frozenset({1}),
            allow_write=True,
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.projects.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = (1, "P1", "p1")
            client = self._client_with_context(context)
            with client:
                response = client.delete("/projects/1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "deleted")

    # ---------------------------------------------------------------------------
    # F. Authorization ordering: no existence information leak through 404/403
    # ---------------------------------------------------------------------------

    def test_unauthorized_access_denied_before_db_lookup_prevents_404_leak(self):
        """For unauthorized callers, both non-existent (999) and existing (1) project IDs
        return 403 before any database query, preventing existence enumeration."""
        context = AuthorizationContext(
            identity="scoped-user",
            allowed_project_ids=frozenset({2}),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.projects.get_db_connection") as mock_db,
        ):
            client = self._client_with_context(context)
            with client:
                # Project 999 does not exist, Project 1 exists, but neither is authorized
                res_nonexistent = client.get("/projects/999")
                res_other = client.get("/projects/1")

        self.assertEqual(res_nonexistent.status_code, 403)
        self.assertEqual(res_other.status_code, 403)
        # Database was never queried for either request
        mock_db.assert_not_called()


if __name__ == "__main__":
    unittest.main()
