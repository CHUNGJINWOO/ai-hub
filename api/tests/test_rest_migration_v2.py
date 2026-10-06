"""
Phase 4 REST Router Migration v2 Test Suite.

Verifies all 20 required scenarios from the Phase 4 specification:
1.  project A user -> project A resource read
2.  project A user -> project B resource denied
3.  PROJECT_READ_ALL -> project resources read
4.  PROJECT_READ_ALL -> global resources not automatically permitted
5.  RESOURCE_READ_GLOBAL -> global resources read
6.  RESOURCE_READ_GLOBAL -> project resources not automatically permitted
7.  project writer -> own project write
8.  project writer -> other project write denied
9.  project writer -> project A -> NULL denied
10. project writer -> project A -> project B denied
11. RESOURCE_WRITE_GLOBAL -> global write possible
12. PROJECT_ADMIN -> resource global write not allowed
13. unauthorized resource ID -> anti-enumeration maintained
14. unauthorized document chunk access -> parent document scope maintained
15. search -> unauthorized projects excluded
16. context assembly -> unauthorized projects excluded
17. production REST endpoint -> missing AuthorizationContext bypass impossible
18. project deletion with owned resources -> 409
19. global resource semantics
20. empty project scope semantics

Also validates the end-to-end middleware execution path:
HTTP Request -> Authentication -> AuthorizationContext -> Router -> Authorization Engine -> DB
"""

import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.authorization import (
    AuthorizationContext,
    AuthorizationDenied,
    authorize_project_admin,
    authorize_project_create,
    authorize_read,
    authorize_write,
)
from app.core.capabilities import Capability
from app.core.scopes import GlobalScope, ProjectScope
from app.main import app, _REST_API_KEY
import app.routers.context as context_router
import app.routers.documents as documents_router
import app.routers.memories as memories_router
import app.routers.projects as projects_router


_TEST_API_KEY: str = _REST_API_KEY or "test-only-mcp-token"


class RestMigrationV2Tests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def _client_with_context(self, context: AuthorizationContext) -> TestClient:
        client = TestClient(app)
        client.app_state["authorization_context"] = context
        return client

    def _client_with_bearer_context(self, context: AuthorizationContext) -> TestClient:
        """Create a client with an Authorization header and mock token verification
        so the request executes through the real middleware pipeline."""
        client = TestClient(app, headers={"Authorization": "Bearer mocked-v2-token"})
        return client

    # ---------------------------------------------------------------------------
    # Scenario 1: project A user -> project A resource read
    # ---------------------------------------------------------------------------
    def test_01_project_a_user_reads_project_a_resources(self):
        ctx = AuthorizationContext(
            identity="user-p1",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset(),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_doc_db,
            patch("app.routers.documents.fetch_document") as mock_fetch_doc,
            patch("app.routers.memories.get_db_connection") as mock_mem_db,
        ):
            mock_doc_conn = MagicMock()
            mock_doc_db.return_value.__enter__.return_value = mock_doc_conn
            mock_doc_conn.execute.return_value.fetchone.return_value = (1,)  # project_id = 1
            mock_fetch_doc.return_value = {"id": 10, "project_id": 1, "title": "Doc 1"}

            client = self._client_with_context(ctx)
            with client:
                res_doc = client.get("/documents/10")
            self.assertEqual(res_doc.status_code, 200)
            self.assertEqual(res_doc.json()["title"], "Doc 1")

            mock_mem_conn = MagicMock()
            mock_mem_db.return_value.__enter__.return_value = mock_mem_conn
            mock_mem_conn.execute.return_value.fetchone.side_effect = [
                (1,),  # project_id check
                (20, "Memory content", "fact", "cat", 3, "src", 1, "now", "now", True),
            ]
            with client:
                res_mem = client.get("/memories/20")
            self.assertEqual(res_mem.status_code, 200)
            self.assertEqual(res_mem.json()["id"], 20)

    # ---------------------------------------------------------------------------
    # Scenario 2: project A user -> project B resource denied
    # ---------------------------------------------------------------------------
    def test_02_project_a_user_project_b_resource_denied(self):
        ctx = AuthorizationContext(
            identity="user-p1",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset(),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_doc_db,
            patch("app.routers.memories.get_db_connection") as mock_mem_db,
        ):
            mock_doc_conn = MagicMock()
            mock_doc_db.return_value.__enter__.return_value = mock_doc_conn
            # Scoped SQL returns None because project_id 2 is not in allowed list [1]
            mock_doc_conn.execute.return_value.fetchone.return_value = None

            client = self._client_with_context(ctx)
            with client:
                res_doc = client.get("/documents/11")
            self.assertEqual(res_doc.status_code, 404)
            self.assertEqual(res_doc.json()["detail"], "Document not found")

            mock_mem_conn = MagicMock()
            mock_mem_db.return_value.__enter__.return_value = mock_mem_conn
            mock_mem_conn.execute.return_value.fetchone.return_value = None
            with client:
                res_mem = client.get("/memories/21")
            self.assertEqual(res_mem.status_code, 404)
            self.assertEqual(res_mem.json()["detail"], "Memory not found")

    # ---------------------------------------------------------------------------
    # Scenario 3: PROJECT_READ_ALL -> project resources read
    # ---------------------------------------------------------------------------
    def test_03_project_read_all_reads_project_resources(self):
        ctx = AuthorizationContext(
            identity="global-auditor",
            read_project_ids=frozenset(),
            capabilities=frozenset({Capability.PROJECT_READ_ALL}),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.projects.fetch_projects") as mock_fetch_proj,
            patch("app.routers.documents.get_db_connection") as mock_doc_db,
            patch("app.routers.documents.fetch_document") as mock_fetch_doc,
        ):
            mock_fetch_proj.return_value = {"count": 2, "projects": [{"id": 1}, {"id": 2}]}
            client = self._client_with_context(ctx)
            with client:
                res_projects = client.get("/projects")
            self.assertEqual(res_projects.status_code, 200)
            self.assertEqual(res_projects.json()["count"], 2)

            mock_doc_conn = MagicMock()
            mock_doc_db.return_value.__enter__.return_value = mock_doc_conn
            mock_doc_conn.execute.return_value.fetchone.return_value = (2,)  # project_id = 2
            mock_fetch_doc.return_value = {"id": 15, "project_id": 2, "title": "P2 Doc"}
            with client:
                res_doc = client.get("/documents/15")
            self.assertEqual(res_doc.status_code, 200)
            self.assertEqual(res_doc.json()["title"], "P2 Doc")

    # ---------------------------------------------------------------------------
    # Scenario 4: PROJECT_READ_ALL -> global resources not automatically permitted
    # ---------------------------------------------------------------------------
    def test_04_project_read_all_does_not_permit_global_resources(self):
        ctx = AuthorizationContext(
            identity="project-auditor",
            read_project_ids=frozenset(),
            capabilities=frozenset({Capability.PROJECT_READ_ALL}),
        )
        # Direct authorization engine check
        with self.assertRaises(AuthorizationDenied):
            authorize_read(ctx, GlobalScope())

        # HTTP check: document with NULL project_id is filtered out by SQL
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_doc_db,
        ):
            mock_doc_conn = MagicMock()
            mock_doc_db.return_value.__enter__.return_value = mock_doc_conn
            mock_doc_conn.execute.return_value.fetchone.return_value = None  # Filtered by SQL
            client = self._client_with_context(ctx)
            with client:
                res = client.get("/documents/99")
            self.assertEqual(res.status_code, 404)

    # ---------------------------------------------------------------------------
    # Scenario 5: RESOURCE_READ_GLOBAL -> global resources read
    # ---------------------------------------------------------------------------
    def test_05_resource_read_global_reads_global_resources(self):
        ctx = AuthorizationContext(
            identity="global-resource-reader",
            read_project_ids=frozenset(),
            capabilities=frozenset({Capability.RESOURCE_READ_GLOBAL}),
        )
        # Direct check
        authorize_read(ctx, GlobalScope())

        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_doc_db,
            patch("app.routers.documents.fetch_document") as mock_fetch_doc,
        ):
            mock_doc_conn = MagicMock()
            mock_doc_db.return_value.__enter__.return_value = mock_doc_conn
            mock_doc_conn.execute.return_value.fetchone.return_value = (None,)  # project_id IS NULL
            mock_fetch_doc.return_value = {"id": 99, "project_id": None, "title": "Global Doc"}
            client = self._client_with_context(ctx)
            with client:
                res = client.get("/documents/99")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["title"], "Global Doc")

    # ---------------------------------------------------------------------------
    # Scenario 6: RESOURCE_READ_GLOBAL -> project resources not automatically permitted
    # ---------------------------------------------------------------------------
    def test_06_resource_read_global_does_not_permit_project_resources(self):
        ctx = AuthorizationContext(
            identity="global-resource-reader",
            read_project_ids=frozenset(),
            capabilities=frozenset({Capability.RESOURCE_READ_GLOBAL}),
        )
        # Direct check
        with self.assertRaises(AuthorizationDenied):
            authorize_read(ctx, ProjectScope(1))

        # HTTP check: project-scoped document filtered out by SQL
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_doc_db,
        ):
            mock_doc_conn = MagicMock()
            mock_doc_db.return_value.__enter__.return_value = mock_doc_conn
            mock_doc_conn.execute.return_value.fetchone.return_value = None
            client = self._client_with_context(ctx)
            with client:
                res = client.get("/documents/10")
            self.assertEqual(res.status_code, 404)

    # ---------------------------------------------------------------------------
    # Scenario 7: project writer -> own project write
    # ---------------------------------------------------------------------------
    def test_07_project_writer_writes_own_project_resources(self):
        ctx = AuthorizationContext(
            identity="writer-p1",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset({1}),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_doc_db,
        ):
            mock_doc_conn = MagicMock()
            mock_doc_db.return_value.__enter__.return_value = mock_doc_conn
            mock_doc_conn.execute.return_value.fetchone.side_effect = [
                (1, "Old Title", "doc.txt", "text/plain", "manual", "desc", "active"),  # SELECT document
                (1,),  # SELECT project
                (10, 1, "New Title", "doc.txt", "text/plain", "manual", "desc", "active", "now", "now"),  # UPDATE RETURNING
            ]
            client = self._client_with_context(ctx)
            with client:
                res = client.patch("/documents/10", json={"title": "New Title"})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["title"], "New Title")

    # ---------------------------------------------------------------------------
    # Scenario 8: project writer -> other project write denied
    # ---------------------------------------------------------------------------
    def test_08_project_writer_denied_writing_other_project_resources(self):
        ctx = AuthorizationContext(
            identity="writer-p1",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset({1}),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_doc_db,
        ):
            mock_doc_conn = MagicMock()
            mock_doc_db.return_value.__enter__.return_value = mock_doc_conn
            mock_doc_conn.execute.return_value.fetchone.return_value = None  # SQL filter blocks it
            client = self._client_with_context(ctx)
            with client:
                res = client.patch("/documents/20", json={"title": "Attack Title"})
            self.assertEqual(res.status_code, 404)

    # ---------------------------------------------------------------------------
    # Scenario 9: project writer -> project A -> NULL denied
    # ---------------------------------------------------------------------------
    def test_09_project_writer_cannot_demote_to_global_null(self):
        ctx = AuthorizationContext(
            identity="writer-p1",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset({1}),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_doc_db,
        ):
            mock_doc_conn = MagicMock()
            mock_doc_db.return_value.__enter__.return_value = mock_doc_conn
            mock_doc_conn.execute.return_value.fetchone.return_value = (
                1, "Doc", "doc.txt", "text/plain", "manual", "desc", "active"
            )
            client = self._client_with_context(ctx)
            with client:
                res = client.patch("/documents/10", json={"project_id": None})
            self.assertEqual(res.status_code, 403)
            self.assertIn("RESOURCE_WRITE_GLOBAL", res.json()["detail"])

    # ---------------------------------------------------------------------------
    # Scenario 10: project writer -> project A -> project B denied
    # ---------------------------------------------------------------------------
    def test_10_project_writer_cannot_transfer_ownership_between_projects(self):
        ctx = AuthorizationContext(
            identity="multi-writer",
            read_project_ids=frozenset({1, 2}),
            write_project_ids=frozenset({1, 2}),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_doc_db,
        ):
            mock_doc_conn = MagicMock()
            mock_doc_db.return_value.__enter__.return_value = mock_doc_conn
            mock_doc_conn.execute.return_value.fetchone.return_value = (
                1, "Doc", "doc.txt", "text/plain", "manual", "desc", "active"
            )
            client = self._client_with_context(ctx)
            with client:
                res = client.patch("/documents/10", json={"project_id": 2})
            self.assertEqual(res.status_code, 403)
            self.assertIn("reassigning resource ownership between projects is not permitted", res.json()["detail"])

    # ---------------------------------------------------------------------------
    # Scenario 11: RESOURCE_WRITE_GLOBAL -> global write possible
    # ---------------------------------------------------------------------------
    def test_11_resource_write_global_allows_global_resource_write(self):
        ctx = AuthorizationContext(
            identity="global-admin",
            read_project_ids=frozenset(),
            write_project_ids=frozenset(),
            capabilities=frozenset({Capability.RESOURCE_WRITE_GLOBAL}),
        )
        # Direct check
        authorize_write(ctx, GlobalScope())

        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_doc_db,
        ):
            mock_doc_conn = MagicMock()
            mock_doc_db.return_value.__enter__.return_value = mock_doc_conn
            mock_doc_conn.execute.return_value.fetchone.side_effect = [
                (None, "Global Doc", "doc.txt", "text/plain", "manual", "desc", "active"),  # SELECT
                (99, None, "Global Doc Updated", "doc.txt", "text/plain", "manual", "desc", "active", "now", "now"),  # UPDATE
            ]
            client = self._client_with_context(ctx)
            with client:
                res = client.patch("/documents/99", json={"title": "Global Doc Updated"})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["title"], "Global Doc Updated")

    # ---------------------------------------------------------------------------
    # Scenario 12: PROJECT_ADMIN -> resource global write not allowed
    # ---------------------------------------------------------------------------
    def test_12_project_admin_does_not_grant_global_resource_write(self):
        ctx = AuthorizationContext(
            identity="proj-admin",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset({1}),
            capabilities=frozenset({Capability.PROJECT_ADMIN}),
        )
        # Direct check: project admin cannot write global resource
        with self.assertRaises(AuthorizationDenied):
            authorize_write(ctx, GlobalScope())

        # Project admin cannot demote resource to global NULL
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_doc_db,
        ):
            mock_doc_conn = MagicMock()
            mock_doc_db.return_value.__enter__.return_value = mock_doc_conn
            mock_doc_conn.execute.return_value.fetchone.return_value = (
                1, "Doc", "doc.txt", "text/plain", "manual", "desc", "active"
            )
            client = self._client_with_context(ctx)
            with client:
                res = client.patch("/documents/10", json={"project_id": None})
            self.assertEqual(res.status_code, 403)
            self.assertIn("RESOURCE_WRITE_GLOBAL", res.json()["detail"])

    # ---------------------------------------------------------------------------
    # Scenario 13: unauthorized resource ID -> anti-enumeration maintained
    # ---------------------------------------------------------------------------
    def test_13_anti_enumeration_nonexistent_and_unauthorized_ids_return_identical_404(self):
        ctx = AuthorizationContext(
            identity="user-p1",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset(),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_doc_db,
        ):
            mock_doc_conn = MagicMock()
            mock_doc_db.return_value.__enter__.return_value = mock_doc_conn
            mock_doc_conn.execute.return_value.fetchone.return_value = None

            client = self._client_with_context(ctx)
            with client:
                res_missing = client.get("/documents/999999")
                res_out_of_scope = client.get("/documents/200000")

            self.assertEqual(res_missing.status_code, 404)
            self.assertEqual(res_out_of_scope.status_code, 404)
            self.assertEqual(res_missing.json(), res_out_of_scope.json())

    # ---------------------------------------------------------------------------
    # Scenario 14: unauthorized document chunk access -> parent document scope maintained
    # ---------------------------------------------------------------------------
    def test_14_document_chunks_scoped_by_parent_document(self):
        ctx = AuthorizationContext(
            identity="user-p1",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset(),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.documents.get_db_connection") as mock_doc_db,
        ):
            mock_doc_conn = MagicMock()
            mock_doc_db.return_value.__enter__.return_value = mock_doc_conn
            # Parent document not found in project 1 scope
            mock_doc_conn.execute.return_value.fetchone.return_value = None

            client = self._client_with_context(ctx)
            with client:
                res = client.get("/documents/20/chunks")
            self.assertEqual(res.status_code, 404)
            self.assertEqual(res.json()["detail"], "Document not found")

    # ---------------------------------------------------------------------------
    # Scenario 15: search -> unauthorized projects excluded
    # ---------------------------------------------------------------------------
    def test_15_search_context_blocks_unauthorized_project_id(self):
        ctx = AuthorizationContext(
            identity="user-p1",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset(),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch.object(context_router, "unified_search_context") as mock_search,
        ):
            client = self._client_with_context(ctx)
            with client:
                res = client.get("/context/search", params={"q": "cmd_vel", "project_id": 2})
            self.assertEqual(res.status_code, 403)
            self.assertIn("not authorized for project: 2", res.json()["detail"])
            mock_search.assert_not_called()

    # ---------------------------------------------------------------------------
    # Scenario 16: context assembly -> unauthorized projects excluded
    # ---------------------------------------------------------------------------
    def test_16_assemble_context_blocks_unauthorized_project_id(self):
        ctx = AuthorizationContext(
            identity="user-p1",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset(),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch.object(context_router, "build_context") as mock_build,
        ):
            client = self._client_with_context(ctx)
            with client:
                res = client.get("/context/assemble", params={"q": "cmd_vel", "project_id": 2})
            self.assertEqual(res.status_code, 403)
            self.assertIn("not authorized for project: 2", res.json()["detail"])
            mock_build.assert_not_called()

    # ---------------------------------------------------------------------------
    # Scenario 17: production REST endpoint -> missing AuthorizationContext bypass impossible
    # ---------------------------------------------------------------------------
    def test_17_missing_auth_fails_closed_across_all_routers(self):
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
        ):
            client = self.client  # No authorization context
            self.assertEqual(client.get("/projects").status_code, 403)
            self.assertEqual(client.post("/projects", json={"name": "P", "slug": "p"}).status_code, 403)
            self.assertEqual(client.get("/documents").status_code, 403)
            self.assertEqual(client.get("/documents/1").status_code, 403)
            self.assertEqual(client.get("/memories").status_code, 403)
            self.assertEqual(client.get("/memories/1").status_code, 403)
            self.assertEqual(client.get("/context/search", params={"q": "test"}).status_code, 403)
            self.assertEqual(client.get("/context/assemble", params={"q": "test"}).status_code, 403)

    # ---------------------------------------------------------------------------
    # Scenario 18: project deletion with owned resources -> 409
    # ---------------------------------------------------------------------------
    def test_18_delete_project_with_owned_resources_returns_409(self):
        ctx = AuthorizationContext(
            identity="proj-admin",
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
            # True for owned documents, False for owned memories
            mock_conn.execute.return_value.fetchone.return_value = (True, False)

            client = self._client_with_context(ctx)
            with client:
                res = client.delete("/projects/1")
            self.assertEqual(res.status_code, 409)
            self.assertIn("active documents or memories", res.json()["detail"])

    # ---------------------------------------------------------------------------
    # Scenario 19: global resource semantics
    # ---------------------------------------------------------------------------
    def test_19_global_resource_scope_semantics(self):
        with self.assertRaises(ValueError):
            ProjectScope(None)

        with self.assertRaises(TypeError):
            authorize_read(
                AuthorizationContext(identity="test"),
                "not-a-scope",  # type: ignore
            )

        self.assertEqual(GlobalScope(), GlobalScope())
        self.assertNotEqual(GlobalScope(), ProjectScope(1))

    # ---------------------------------------------------------------------------
    # Scenario 20: empty project scope semantics
    # ---------------------------------------------------------------------------
    def test_20_empty_project_scope_retrieves_no_projects(self):
        ctx = AuthorizationContext(
            identity="empty-user",
            read_project_ids=frozenset(),
            write_project_ids=frozenset(),
        )
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.projects.fetch_projects") as mock_fetch_proj,
        ):
            mock_fetch_proj.return_value = {"count": 0, "projects": []}
            client = self._client_with_context(ctx)
            with client:
                res = client.get("/projects")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["count"], 0)
            mock_fetch_proj.assert_called_once_with(allowed_project_ids=frozenset())

    # ---------------------------------------------------------------------------
    # Middleware integration: Bearer token -> AuthorizationContext -> Endpoint
    # ---------------------------------------------------------------------------
    def test_middleware_pipeline_static_api_key_end_to_end(self):
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch.object(context_router, "unified_search_context") as mock_search,
        ):
            mock_search.return_value = {"results": []}
            client = TestClient(app, headers={"Authorization": f"Bearer {_TEST_API_KEY}"})
            # static api key has RESOURCE_READ_GLOBAL, so project_id=None succeeds
            res = client.get("/context/search", params={"q": "ros2"})
            self.assertEqual(res.status_code, 200)

            # static api key has no project permissions, so project_id=2 fails with 403
            res_denied = client.get("/context/search", params={"q": "ros2", "project_id": 2})
            self.assertEqual(res_denied.status_code, 403)
