"""
Tests for Resource Ownership Policy & Foreign Key Protection (Phase 3).

Verifies:
1. project deletion with owned document -> 409 Conflict
2. project deletion with owned memory -> 409 Conflict
3. project deletion with no owned resources -> 200 Success
4. deleting project with database FK violation -> 409 Conflict
5. memory project_id -> NULL without RESOURCE_WRITE_GLOBAL -> 403 Forbidden
6. document project_id -> NULL without RESOURCE_WRITE_GLOBAL -> 403 Forbidden
7. Project A -> Project B reassignment -> 403 Forbidden
8. same project_id reassignment/no-op -> Allowed
9. global resource unlinking with RESOURCE_WRITE_GLOBAL -> Allowed
10. PROJECT_ADMIN alone cannot unlink to NULL -> 403 Forbidden
11. migration 0002 contract compliance
"""

from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import psycopg

from app.core.capabilities import Capability
from app.core.authorization import AuthorizationContext
from app.core.migration_contract import load_migration_files, parse_migration_filename
from app.main import app


class ResourceOwnershipPolicyTests(unittest.TestCase):
    def _client_with_context(self, context: AuthorizationContext) -> TestClient:
        client = TestClient(app)
        client.app_state["authorization_context"] = context
        return client

    # -----------------------------------------------------------------------
    # 1. Project Deletion Policy (409 Conflict vs 200 Success)
    # -----------------------------------------------------------------------

    def test_delete_project_with_owned_document_returns_409(self):
        """Project with owned documents cannot be deleted and returns 409."""
        ctx = AuthorizationContext(
            identity="admin",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset({1}),
            capabilities=frozenset({Capability.PROJECT_ADMIN}),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.projects.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            # has_docs = True, has_mems = False
            mock_conn.execute.return_value.fetchone.return_value = (True, False)

            with self._client_with_context(ctx) as client:
                response = client.delete("/projects/1")

        self.assertEqual(response.status_code, 409)
        self.assertIn("active documents or memories", response.json()["detail"])

    def test_delete_project_with_owned_memory_returns_409(self):
        """Project with owned memories cannot be deleted and returns 409."""
        ctx = AuthorizationContext(
            identity="admin",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset({1}),
            capabilities=frozenset({Capability.PROJECT_ADMIN}),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.projects.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            # has_docs = False, has_mems = True
            mock_conn.execute.return_value.fetchone.return_value = (False, True)

            with self._client_with_context(ctx) as client:
                response = client.delete("/projects/1")

        self.assertEqual(response.status_code, 409)
        self.assertIn("active documents or memories", response.json()["detail"])

    def test_delete_project_with_no_owned_resources_succeeds(self):
        """Empty project deletion succeeds without conflict."""
        ctx = AuthorizationContext(
            identity="admin",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset({1}),
            capabilities=frozenset({Capability.PROJECT_ADMIN}),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.projects.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.side_effect = [
                (False, False),  # EXISTS check
                (1, "Empty Project", "empty-proj"),  # DELETE returning
            ]

            with self._client_with_context(ctx) as client:
                response = client.delete("/projects/1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "deleted")

    def test_delete_project_fk_violation_returns_409(self):
        """Database foreign key violation during project deletion returns 409."""
        ctx = AuthorizationContext(
            identity="admin",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset({1}),
            capabilities=frozenset({Capability.PROJECT_ADMIN}),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.projects.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.side_effect = [
                MagicMock(fetchone=lambda: (False, False)),  # Passed pre-check
                psycopg.errors.ForeignKeyViolation("update or delete on table projects violates foreign key constraint"),
            ]

            with self._client_with_context(ctx) as client:
                response = client.delete("/projects/1")

        self.assertEqual(response.status_code, 409)
        self.assertIn("active documents or memories", response.json()["detail"])

    # -----------------------------------------------------------------------
    # 2. Ownership Mutation Policy for Memories
    # -----------------------------------------------------------------------

    def test_memory_unlink_to_null_without_resource_write_global_denied(self):
        """Unlinking memory to NULL without RESOURCE_WRITE_GLOBAL returns 403."""
        ctx = AuthorizationContext(
            identity="writer",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset({1}),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.memories.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = (
                "Old Content", "fact", None, 3, "api", 1  # project_id = 1
            )

            with self._client_with_context(ctx) as client:
                response = client.patch("/memories/10", json={"project_id": None})

        self.assertEqual(response.status_code, 403)
        self.assertIn("RESOURCE_WRITE_GLOBAL", response.json()["detail"])

    def test_memory_project_reassignment_between_projects_denied(self):
        """Reassigning memory from Project 1 to Project 2 is denied (403)."""
        ctx = AuthorizationContext(
            identity="writer",
            read_project_ids=frozenset({1, 2}),
            write_project_ids=frozenset({1, 2}),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.memories.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = (
                "Old Content", "fact", None, 3, "api", 1  # project_id = 1
            )

            with self._client_with_context(ctx) as client:
                response = client.patch("/memories/10", json={"project_id": 2})

        self.assertEqual(response.status_code, 403)
        self.assertIn("reassigning resource ownership", response.json()["detail"])

    def test_memory_same_project_id_reassignment_allowed(self):
        """Sending the same existing project_id is allowed."""
        ctx = AuthorizationContext(
            identity="writer",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset({1}),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.memories.get_db_connection") as mock_db,
            patch("app.routers.memories.model"),
            patch("app.routers.memories.Vector"),
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.side_effect = [
                ("Old Content", "fact", None, 3, "api", 1),  # current
                (1,),  # project existence check
                (10, "Old Content", "fact", None, 3, "api", "now", "now", 1),  # update returning
            ]

            with self._client_with_context(ctx) as client:
                response = client.patch("/memories/10", json={"project_id": 1})

        self.assertEqual(response.status_code, 200)

    def test_memory_unlink_to_null_with_resource_write_global_succeeds(self):
        """Caller with RESOURCE_WRITE_GLOBAL can unlink memory to NULL."""
        ctx = AuthorizationContext(
            identity="global-admin",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset({1}),
            capabilities=frozenset({Capability.RESOURCE_WRITE_GLOBAL}),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.memories.get_db_connection") as mock_db,
            patch("app.routers.memories.model"),
            patch("app.routers.memories.Vector"),
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.side_effect = [
                ("Old Content", "fact", None, 3, "api", 1),  # current
                (10, "Old Content", "fact", None, 3, "api", "now", "now", None),  # update returning
            ]

            with self._client_with_context(ctx) as client:
                response = client.patch("/memories/10", json={"project_id": None})

        self.assertEqual(response.status_code, 200)

    # -----------------------------------------------------------------------
    # 3. Ownership Mutation Policy for Documents
    # -----------------------------------------------------------------------

    def test_document_unlink_to_null_without_resource_write_global_denied(self):
        """Unlinking document to NULL without RESOURCE_WRITE_GLOBAL returns 403."""
        ctx = AuthorizationContext(
            identity="writer",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset({1}),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = (
                1, "Title", "doc.txt", "text/plain", "api", None, "active"  # project_id = 1
            )

            with self._client_with_context(ctx) as client:
                response = client.patch("/documents/5", json={"project_id": None})

        self.assertEqual(response.status_code, 403)
        self.assertIn("RESOURCE_WRITE_GLOBAL", response.json()["detail"])

    def test_document_project_reassignment_between_projects_denied(self):
        """Reassigning document from Project 1 to Project 2 is denied (403)."""
        ctx = AuthorizationContext(
            identity="writer",
            read_project_ids=frozenset({1, 2}),
            write_project_ids=frozenset({1, 2}),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = (
                1, "Title", "doc.txt", "text/plain", "api", None, "active"  # project_id = 1
            )

            with self._client_with_context(ctx) as client:
                response = client.patch("/documents/5", json={"project_id": 2})

        self.assertEqual(response.status_code, 403)
        self.assertIn("reassigning resource ownership", response.json()["detail"])

    def test_document_unlink_to_null_with_resource_write_global_succeeds(self):
        """Caller with RESOURCE_WRITE_GLOBAL can unlink document to NULL."""
        ctx = AuthorizationContext(
            identity="global-admin",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset({1}),
            capabilities=frozenset({Capability.RESOURCE_WRITE_GLOBAL}),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.side_effect = [
                (1, "Title", "doc.txt", "text/plain", "api", None, "active"),  # current
                (5, None, "Title", "doc.txt", "text/plain", "api", None, "active", "now", "now"),  # update returning
            ]

            with self._client_with_context(ctx) as client:
                response = client.patch("/documents/5", json={"project_id": None})

        self.assertEqual(response.status_code, 200)

    def test_project_admin_alone_cannot_unlink_to_null(self):
        """PROJECT_ADMIN capability without RESOURCE_WRITE_GLOBAL is rejected for unlinking."""
        ctx = AuthorizationContext(
            identity="admin",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset({1}),
            capabilities=frozenset({Capability.PROJECT_ADMIN}),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_db,
        ):
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = (
                1, "Title", "doc.txt", "text/plain", "api", None, "active"
            )

            with self._client_with_context(ctx) as client:
                response = client.patch("/documents/5", json={"project_id": None})

        self.assertEqual(response.status_code, 403)
        self.assertIn("RESOURCE_WRITE_GLOBAL", response.json()["detail"])

    # -----------------------------------------------------------------------
    # 4. Migration 0002 Contract Validation
    # -----------------------------------------------------------------------

    def test_migration_0002_matches_revision_contract(self):
        """0002_restrict_project_foreign_keys.sql conforms to MIGRATION_ARCHITECTURE.md contract."""
        migration_file = Path(__file__).parents[2] / "migrations" / "0002_restrict_project_foreign_keys.sql"
        self.assertTrue(migration_file.exists())

        revision, slug = parse_migration_filename(migration_file.name)
        self.assertEqual(revision, 2)
        self.assertEqual(slug, "restrict_project_foreign_keys")

        baseline_file = Path(__file__).parents[2] / "migrations" / "0001_baseline.sql"
        migrations = load_migration_files([baseline_file, migration_file])
        self.assertEqual([m.revision for m in migrations], [1, 2])


if __name__ == "__main__":
    unittest.main()

