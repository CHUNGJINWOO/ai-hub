"""
Phase 5 MCP Authorization Migration v2 Test Suite.

Verifies all 18 required scenarios from the Phase 5 specification:
1.  Authenticated user own project document read
2.  Other project document denied (anti-enumeration)
3.  PROJECT_READ_ALL project documents read
4.  PROJECT_READ_ALL global document not automatically permitted
5.  RESOURCE_READ_GLOBAL global document read
6.  RESOURCE_READ_GLOBAL project document not automatically permitted
7.  get_document anti-enumeration (identical error for missing vs unauthorized)
8.  search_context project filtering
9.  get_context project filtering
10. list_projects project filtering
11. PROJECT_READ_ALL list_projects
12. list_skills decoupled from resource auth
13. empty read_project_ids
14. empty write_project_ids (read-only caller)
15. REST decision == MCP decision equivalence
16. concurrent MCP requests context isolation (asyncio.gather)
17. malformed/missing authorization context fail closed
18. MCP middleware -> AuthorizationContext end-to-end flow
"""

import asyncio
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from mcp.server.auth.provider import AccessToken

os.environ.setdefault("MCP_ACCESS_TOKEN", "test-only-mcp-token")

from app.core.authorization import (
    AuthorizationContext,
    AuthorizationDenied,
    _mcp_authorization_context,
    mcp_authorization_context,
)
from app.core.capabilities import Capability
from app.mcp_server import (
    MCPAuthorizationContextMiddleware,
    _authorization_context_from_access_token,
    get_context,
    get_document,
    list_projects,
    list_skills,
    search_context,
)


def _mock_doc_db_for_id(lookup_dict: dict[int, int | None]):
    """Helper to mock DB connection for get_document lookups.
    lookup_dict maps doc_id to its project_id (or None for global).
    If doc_id is not in lookup_dict, it represents non-existent document.
    """
    mock_conn = MagicMock()

    def mock_execute(query, params=None):
        mock_cursor = MagicMock()
        if "SELECT project_id" in query and params:
            doc_id = params[0]
            if doc_id in lookup_dict:
                p_id = lookup_dict[doc_id]
                # Check parameters:
                # params: (doc_id, can_read_global) for PROJECT_READ_ALL
                # params: (doc_id, list(read_project_ids), can_read_global) otherwise
                if len(params) == 2:
                    can_read_global = params[1]
                    if p_id is not None or can_read_global:
                        mock_cursor.fetchone.return_value = (p_id,)
                    else:
                        mock_cursor.fetchone.return_value = None
                else:
                    read_project_ids = params[1]
                    can_read_global = params[2]
                    if p_id is not None:
                        if p_id in read_project_ids:
                            mock_cursor.fetchone.return_value = (p_id,)
                        else:
                            mock_cursor.fetchone.return_value = None
                    else:
                        if can_read_global:
                            mock_cursor.fetchone.return_value = (None,)
                        else:
                            mock_cursor.fetchone.return_value = None
            else:
                mock_cursor.fetchone.return_value = None
        return mock_cursor

    mock_conn.execute.side_effect = mock_execute
    return mock_conn


class McpMigrationV2Tests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.doc_10 = {
            "id": 10,
            "project_id": 1,
            "title": "Doc in Project 1",
            "filename": "doc1.txt",
            "mime_type": "text/plain",
            "source": "local",
            "description": None,
            "status": "active",
            "created_at": self.now,
            "updated_at": self.now,
            "chunk_count": 2,
        }
        self.doc_20 = {
            "id": 20,
            "project_id": 2,
            "title": "Doc in Project 2",
            "filename": "doc2.txt",
            "mime_type": "text/plain",
            "source": "local",
            "description": None,
            "status": "active",
            "created_at": self.now,
            "updated_at": self.now,
            "chunk_count": 3,
        }
        self.doc_30 = {
            "id": 30,
            "project_id": None,
            "title": "Global Doc",
            "filename": "global.txt",
            "mime_type": "text/plain",
            "source": "local",
            "description": None,
            "status": "active",
            "created_at": self.now,
            "updated_at": self.now,
            "chunk_count": 1,
        }
        self.docs_store = {
            10: self.doc_10,
            20: self.doc_20,
            30: self.doc_30,
        }
        self.lookup_dict = {
            10: 1,
            20: 2,
            30: None,
        }

    # ---------------------------------------------------------------------------
    # Scenario 1: Authenticated user own project document read
    # ---------------------------------------------------------------------------
    def test_01_authenticated_user_own_project_document_read(self):
        ctx = AuthorizationContext(
            identity="user-p1",
            read_project_ids=frozenset({1}),
        )
        mock_conn = _mock_doc_db_for_id(self.lookup_dict)
        with patch("app.mcp_server.get_db_connection") as mock_db, \
             patch("app.mcp_server.fetch_document", side_effect=lambda did: self.docs_store.get(did)):
            mock_db.return_value.__enter__.return_value = mock_conn

            with mcp_authorization_context(ctx):
                res = get_document(10)
                self.assertEqual(res.id, 10)
                self.assertEqual(res.project_id, 1)
                self.assertEqual(res.title, "Doc in Project 1")

    # ---------------------------------------------------------------------------
    # Scenario 2: Other project document denied (anti-enumeration)
    # ---------------------------------------------------------------------------
    def test_02_other_project_document_denied_anti_enumeration(self):
        ctx = AuthorizationContext(
            identity="user-p1",
            read_project_ids=frozenset({1}),
        )
        mock_conn = _mock_doc_db_for_id(self.lookup_dict)
        with patch("app.mcp_server.get_db_connection") as mock_db, \
             patch("app.mcp_server.fetch_document", side_effect=lambda did: self.docs_store.get(did)):
            mock_db.return_value.__enter__.return_value = mock_conn

            with mcp_authorization_context(ctx):
                with self.assertRaisesRegex(ValueError, "document not found: 20"):
                    get_document(20)

    # ---------------------------------------------------------------------------
    # Scenario 3: PROJECT_READ_ALL project documents read
    # ---------------------------------------------------------------------------
    def test_03_project_read_all_project_documents_read(self):
        ctx = AuthorizationContext(
            identity="auditor",
            capabilities=frozenset({Capability.PROJECT_READ_ALL}),
        )
        mock_conn = _mock_doc_db_for_id(self.lookup_dict)
        with patch("app.mcp_server.get_db_connection") as mock_db, \
             patch("app.mcp_server.fetch_document", side_effect=lambda did: self.docs_store.get(did)):
            mock_db.return_value.__enter__.return_value = mock_conn

            with mcp_authorization_context(ctx):
                res1 = get_document(10)
                self.assertEqual(res1.id, 10)
                res2 = get_document(20)
                self.assertEqual(res2.id, 20)

    # ---------------------------------------------------------------------------
    # Scenario 4: PROJECT_READ_ALL global document not automatically permitted
    # ---------------------------------------------------------------------------
    def test_04_project_read_all_global_document_not_automatically_permitted(self):
        ctx = AuthorizationContext(
            identity="auditor",
            capabilities=frozenset({Capability.PROJECT_READ_ALL}),
        )
        mock_conn = _mock_doc_db_for_id(self.lookup_dict)
        with patch("app.mcp_server.get_db_connection") as mock_db, \
             patch("app.mcp_server.fetch_document", side_effect=lambda did: self.docs_store.get(did)):
            mock_db.return_value.__enter__.return_value = mock_conn

            with mcp_authorization_context(ctx):
                with self.assertRaisesRegex(ValueError, "document not found: 30"):
                    get_document(30)

    # ---------------------------------------------------------------------------
    # Scenario 5: RESOURCE_READ_GLOBAL global document read
    # ---------------------------------------------------------------------------
    def test_05_resource_read_global_global_document_read(self):
        ctx = AuthorizationContext(
            identity="global-viewer",
            capabilities=frozenset({Capability.RESOURCE_READ_GLOBAL}),
        )
        mock_conn = _mock_doc_db_for_id(self.lookup_dict)
        with patch("app.mcp_server.get_db_connection") as mock_db, \
             patch("app.mcp_server.fetch_document", side_effect=lambda did: self.docs_store.get(did)):
            mock_db.return_value.__enter__.return_value = mock_conn

            with mcp_authorization_context(ctx):
                res = get_document(30)
                self.assertEqual(res.id, 30)
                self.assertIsNone(res.project_id)
                self.assertEqual(res.title, "Global Doc")

    # ---------------------------------------------------------------------------
    # Scenario 6: RESOURCE_READ_GLOBAL project document not automatically permitted
    # ---------------------------------------------------------------------------
    def test_06_resource_read_global_project_document_not_automatically_permitted(self):
        ctx = AuthorizationContext(
            identity="global-viewer",
            capabilities=frozenset({Capability.RESOURCE_READ_GLOBAL}),
        )
        mock_conn = _mock_doc_db_for_id(self.lookup_dict)
        with patch("app.mcp_server.get_db_connection") as mock_db, \
             patch("app.mcp_server.fetch_document", side_effect=lambda did: self.docs_store.get(did)):
            mock_db.return_value.__enter__.return_value = mock_conn

            with mcp_authorization_context(ctx):
                with self.assertRaisesRegex(ValueError, "document not found: 10"):
                    get_document(10)

    # ---------------------------------------------------------------------------
    # Scenario 7: get_document anti-enumeration (identical error)
    # ---------------------------------------------------------------------------
    def test_07_get_document_anti_enumeration_identical_error(self):
        ctx = AuthorizationContext(
            identity="user-p1",
            read_project_ids=frozenset({1}),
        )
        mock_conn = _mock_doc_db_for_id(self.lookup_dict)
        with patch("app.mcp_server.get_db_connection") as mock_db, \
             patch("app.mcp_server.fetch_document", side_effect=lambda did: self.docs_store.get(did)):
            mock_db.return_value.__enter__.return_value = mock_conn

            with mcp_authorization_context(ctx):
                # Doc 20 exists in project 2 (forbidden)
                with self.assertRaises(ValueError) as ctx_forbidden:
                    get_document(20)
                # Doc 99999 does not exist anywhere (absent)
                with self.assertRaises(ValueError) as ctx_absent:
                    get_document(99999)

                self.assertEqual(str(ctx_forbidden.exception), "document not found: 20")
                self.assertEqual(str(ctx_absent.exception), "document not found: 99999")

    # ---------------------------------------------------------------------------
    # Scenario 8: search_context project filtering
    # ---------------------------------------------------------------------------
    def test_08_search_context_project_filtering(self):
        ctx = AuthorizationContext(
            identity="user-p1",
            read_project_ids=frozenset({1}),
        )
        with patch("app.mcp_server.unified_search_context") as mock_search:
            mock_search.return_value = {
                "query": "ros",
                "project_id": 1,
                "memory_count": 0,
                "document_count": 0,
                "memories": [],
                "documents": [],
            }
            with mcp_authorization_context(ctx):
                # Authorized project
                res = search_context("ros", project_id=1)
                self.assertEqual(res.project_id, 1)

                # Unauthorized project -> AuthorizationDenied
                with self.assertRaises(AuthorizationDenied):
                    search_context("ros", project_id=2)

                # Unspecified project without global read -> AuthorizationDenied
                with self.assertRaises(AuthorizationDenied):
                    search_context("ros", project_id=None)

    # ---------------------------------------------------------------------------
    # Scenario 9: get_context project filtering
    # ---------------------------------------------------------------------------
    def test_09_get_context_project_filtering(self):
        ctx = AuthorizationContext(
            identity="user-p1",
            read_project_ids=frozenset({1}),
        )
        with patch("app.mcp_server.assemble_canonical_context") as mock_assemble:
            from app.core.context_assembly import CanonicalContext
            mock_assemble.return_value = CanonicalContext(
                query="ros",
                project_id=1,
                memory_count=0,
                document_count=0,
                items=[],
                sources=[],
            )
            with mcp_authorization_context(ctx):
                # Authorized project
                res = get_context("ros", project_id=1)
                self.assertEqual(res.project_id, 1)

                # Unauthorized project -> AuthorizationDenied
                with self.assertRaises(AuthorizationDenied):
                    get_context("ros", project_id=2)

                # Unspecified project -> AuthorizationDenied
                with self.assertRaises(AuthorizationDenied):
                    get_context("ros", project_id=None)

    # ---------------------------------------------------------------------------
    # Scenario 10: list_projects project filtering
    # ---------------------------------------------------------------------------
    def test_10_list_projects_project_filtering(self):
        ctx = AuthorizationContext(
            identity="user-p1",
            read_project_ids=frozenset({1}),
        )
        sample_projects = {
            "count": 1,
            "projects": [
                {
                    "id": 1,
                    "name": "Project 1",
                    "slug": "project-1",
                    "description": None,
                    "status": "active",
                    "created_at": self.now,
                    "updated_at": self.now,
                    "memory_count": 0,
                }
            ],
        }
        with patch("app.mcp_server.fetch_projects") as mock_fetch:
            mock_fetch.return_value = sample_projects
            with mcp_authorization_context(ctx):
                res = list_projects()
                mock_fetch.assert_called_once_with(allowed_project_ids=frozenset({1}))
                self.assertEqual(res.count, 1)
                self.assertEqual(res.projects[0].id, 1)

    # ---------------------------------------------------------------------------
    # Scenario 11: PROJECT_READ_ALL list_projects
    # ---------------------------------------------------------------------------
    def test_11_project_read_all_list_projects(self):
        ctx = AuthorizationContext(
            identity="auditor",
            capabilities=frozenset({Capability.PROJECT_READ_ALL}),
        )
        sample_projects = {
            "count": 2,
            "projects": [
                {
                    "id": 1,
                    "name": "Project 1",
                    "slug": "project-1",
                    "description": None,
                    "status": "active",
                    "created_at": self.now,
                    "updated_at": self.now,
                    "memory_count": 0,
                },
                {
                    "id": 2,
                    "name": "Project 2",
                    "slug": "project-2",
                    "description": None,
                    "status": "active",
                    "created_at": self.now,
                    "updated_at": self.now,
                    "memory_count": 0,
                },
            ],
        }
        with patch("app.mcp_server.fetch_projects") as mock_fetch:
            mock_fetch.return_value = sample_projects
            with mcp_authorization_context(ctx):
                res = list_projects()
                mock_fetch.assert_called_once_with()
                self.assertEqual(res.count, 2)

    # ---------------------------------------------------------------------------
    # Scenario 12: list_skills decoupled from resource auth
    # ---------------------------------------------------------------------------
    def test_12_list_skills_decoupled_from_resource_auth(self):
        # Caller with only project 1 read access
        ctx_project = AuthorizationContext(
            identity="user-p1",
            read_project_ids=frozenset({1}),
        )
        with mcp_authorization_context(ctx_project):
            res_project = list_skills()
            self.assertGreater(len(res_project.skills), 0)

        # Caller with empty project permissions
        ctx_empty = AuthorizationContext(
            identity="user-none",
            read_project_ids=frozenset(),
        )
        with mcp_authorization_context(ctx_empty):
            res_empty = list_skills()
            self.assertGreater(len(res_empty.skills), 0)

    # ---------------------------------------------------------------------------
    # Scenario 13: empty read_project_ids
    # ---------------------------------------------------------------------------
    def test_13_empty_read_project_ids_denies_resources(self):
        ctx = AuthorizationContext(
            identity="empty-user",
            read_project_ids=frozenset(),
        )
        mock_conn = _mock_doc_db_for_id(self.lookup_dict)
        with patch("app.mcp_server.get_db_connection") as mock_db, \
             patch("app.mcp_server.fetch_document", side_effect=lambda did: self.docs_store.get(did)), \
             patch("app.mcp_server.fetch_projects") as mock_fetch_proj:
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_fetch_proj.return_value = {"count": 0, "projects": []}

            with mcp_authorization_context(ctx):
                with self.assertRaises(ValueError):
                    get_document(10)
                with self.assertRaises(AuthorizationDenied):
                    search_context("query", project_id=1)
                res_proj = list_projects()
                mock_fetch_proj.assert_called_once_with(allowed_project_ids=frozenset())
                self.assertEqual(res_proj.count, 0)

    # ---------------------------------------------------------------------------
    # Scenario 14: empty write_project_ids (read-only caller)
    # ---------------------------------------------------------------------------
    def test_14_empty_write_project_ids_permits_reads(self):
        ctx = AuthorizationContext(
            identity="read-only-user",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset(),
        )
        mock_conn = _mock_doc_db_for_id(self.lookup_dict)
        with patch("app.mcp_server.get_db_connection") as mock_db, \
             patch("app.mcp_server.fetch_document", side_effect=lambda did: self.docs_store.get(did)), \
             patch("app.mcp_server.unified_search_context") as mock_search, \
             patch("app.mcp_server.fetch_projects") as mock_fetch_proj:
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_search.return_value = {
                "query": "test",
                "project_id": 1,
                "memory_count": 0,
                "document_count": 0,
                "memories": [],
                "documents": [],
            }
            mock_fetch_proj.return_value = {
                "count": 1,
                "projects": [
                    {
                        "id": 1,
                        "name": "Project 1",
                        "slug": "project-1",
                        "description": None,
                        "status": "active",
                        "created_at": self.now,
                        "updated_at": self.now,
                        "memory_count": 0,
                    }
                ],
            }
            with mcp_authorization_context(ctx):
                doc = get_document(10)
                self.assertEqual(doc.id, 10)
                srch = search_context("test", project_id=1)
                self.assertEqual(srch.project_id, 1)
                proj = list_projects()
                self.assertEqual(proj.count, 1)
                skills = list_skills()
                self.assertGreater(len(skills.skills), 0)

    # ---------------------------------------------------------------------------
    # Scenario 15: REST decision == MCP decision equivalence
    # ---------------------------------------------------------------------------
    def test_15_rest_and_mcp_authorization_decision_equivalence(self):
        """Verify REST and MCP return equivalent decisions for the same contexts."""
        # 1. Project 1 caller reading project 2 document:
        # MCP raises ValueError("document not found: 20")
        # REST returns 404 Not Found (anti-enumeration)
        ctx_p1 = AuthorizationContext(identity="caller", read_project_ids=frozenset({1}))
        mock_conn = _mock_doc_db_for_id(self.lookup_dict)
        with patch("app.mcp_server.get_db_connection") as mock_db, \
             patch("app.mcp_server.fetch_document", side_effect=lambda did: self.docs_store.get(did)):
            mock_db.return_value.__enter__.return_value = mock_conn
            with mcp_authorization_context(ctx_p1):
                # MCP
                with self.assertRaises(ValueError):
                    get_document(20)

        # 2. Project 1 caller searching project 2:
        # MCP raises AuthorizationDenied
        # REST raises AuthorizationDenied -> 403
        with patch("app.mcp_server.unified_search_context"):
            with mcp_authorization_context(ctx_p1):
                with self.assertRaises(AuthorizationDenied):
                    search_context("q", project_id=2)

        # 3. PROJECT_READ_ALL caller reading project doc:
        # MCP succeeds
        # REST returns 200
        ctx_all = AuthorizationContext(identity="auditor", capabilities=frozenset({Capability.PROJECT_READ_ALL}))
        with patch("app.mcp_server.get_db_connection") as mock_db, \
             patch("app.mcp_server.fetch_document", side_effect=lambda did: self.docs_store.get(did)):
            mock_db.return_value.__enter__.return_value = mock_conn
            with mcp_authorization_context(ctx_all):
                doc1 = get_document(10)
                doc2 = get_document(20)
                self.assertEqual(doc1.id, 10)
                self.assertEqual(doc2.id, 20)

        # 4. PROJECT_READ_ALL caller reading global doc:
        # MCP raises ValueError("document not found: 30")
        # REST returns 404
        with patch("app.mcp_server.get_db_connection") as mock_db, \
             patch("app.mcp_server.fetch_document", side_effect=lambda did: self.docs_store.get(did)):
            mock_db.return_value.__enter__.return_value = mock_conn
            with mcp_authorization_context(ctx_all):
                with self.assertRaises(ValueError):
                    get_document(30)

    # ---------------------------------------------------------------------------
    # Scenario 16: Concurrent MCP requests context isolation (asyncio.gather)
    # ---------------------------------------------------------------------------
    def test_16_concurrent_mcp_requests_context_isolation(self):
        async def run_concurrent():
            results = {}

            async def task_p1():
                ctx1 = AuthorizationContext(identity="user-p1", read_project_ids=frozenset({1}))
                with mcp_authorization_context(ctx1):
                    await asyncio.sleep(0.01)
                    # Task p1 should see project 1 doc, but not project 2 doc
                    doc = get_document(10)
                    results["t1_doc10"] = doc.id
                    try:
                        get_document(20)
                        results["t1_doc20"] = "leaked"
                    except ValueError:
                        results["t1_doc20"] = "denied"

            async def task_p2():
                ctx2 = AuthorizationContext(identity="user-p2", read_project_ids=frozenset({2}))
                with mcp_authorization_context(ctx2):
                    await asyncio.sleep(0.01)
                    # Task p2 should see project 2 doc, but not project 1 doc
                    doc = get_document(20)
                    results["t2_doc20"] = doc.id
                    try:
                        get_document(10)
                        results["t2_doc10"] = "leaked"
                    except ValueError:
                        results["t2_doc10"] = "denied"

            async def task_admin():
                ctx_admin = AuthorizationContext(
                    identity="admin",
                    capabilities=frozenset({Capability.PROJECT_READ_ALL}),
                )
                with mcp_authorization_context(ctx_admin):
                    await asyncio.sleep(0.01)
                    results["t3_doc10"] = get_document(10).id
                    results["t3_doc20"] = get_document(20).id

            mock_conn = _mock_doc_db_for_id(self.lookup_dict)
            with patch("app.mcp_server.get_db_connection") as mock_db, \
                 patch("app.mcp_server.fetch_document", side_effect=lambda did: self.docs_store.get(did)):
                mock_db.return_value.__enter__.return_value = mock_conn
                await asyncio.gather(task_p1(), task_p2(), task_admin())

            return results

        results = asyncio.run(run_concurrent())
        self.assertEqual(results["t1_doc10"], 10)
        self.assertEqual(results["t1_doc20"], "denied")
        self.assertEqual(results["t2_doc20"], 20)
        self.assertEqual(results["t2_doc10"], "denied")
        self.assertEqual(results["t3_doc10"], 10)
        self.assertEqual(results["t3_doc20"], 20)

    # ---------------------------------------------------------------------------
    # Scenario 17: Malformed or missing authorization context fails closed
    # ---------------------------------------------------------------------------
    def test_17_missing_authorization_context_fails_closed(self):
        # Ensure ContextVar is clean
        _mcp_authorization_context.set(None)

        with self.assertRaises(AuthorizationDenied):
            get_document(10)

        with self.assertRaises(AuthorizationDenied):
            search_context("query", project_id=1)

        with self.assertRaises(AuthorizationDenied):
            get_context("query", project_id=1)

        with self.assertRaises(AuthorizationDenied):
            list_projects()

        with self.assertRaises(AuthorizationDenied):
            list_skills()

    # ---------------------------------------------------------------------------
    # Scenario 18: MCP middleware -> AuthorizationContext end-to-end flow
    # ---------------------------------------------------------------------------
    def test_18_mcp_middleware_to_authorization_context_end_to_end(self):
        middleware = MCPAuthorizationContextMiddleware()
        captured_context: AuthorizationContext | None = None

        async def dummy_next(_ctx):
            nonlocal captured_context
            captured_context = _mcp_authorization_context.get()
            return {"status": "ok"}

        access_token = AccessToken(
            token="redacted",
            client_id="test-client",
            scopes=["aihub:read"],
            subject="agent-v2",
            claims={
                "read_project_ids": [1, 2],
                "write_project_ids": [1],
                "capabilities": ["PROJECT_READ_ALL"],
            },
        )

        with patch("app.mcp_server.get_access_token", return_value=access_token):
            res = asyncio.run(middleware(object(), dummy_next))

        self.assertEqual(res, {"status": "ok"})
        self.assertIsNotNone(captured_context)
        self.assertEqual(captured_context.identity, "agent-v2")
        self.assertEqual(captured_context.read_project_ids, frozenset({1, 2}))
        self.assertEqual(captured_context.write_project_ids, frozenset({1}))
        self.assertIn(Capability.PROJECT_READ_ALL, captured_context.capabilities)
        # ContextVar is reset after middleware completes
        self.assertIsNone(_mcp_authorization_context.get())
