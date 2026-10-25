"""
M3-D7 Real PostgreSQL 17 + pgvector Live Integration Test Suite.

Executes end-to-end integration tests directly against a live, isolated
PostgreSQL 17.11 instance with pgvector 0.8.7.

Safety Guarantees:
- Enforces strict target DB check: fails immediately if database is not 'ai_hub_test_m3d7'.
- Enforces PG major version check (17).
- Uses isolated temporary upload directories for file safety tests.
- Strictly isolated from production database, production ports, and production data.
"""

import concurrent.futures
import hashlib
import io
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient

# Ensure test environment variables before application imports
os.environ.setdefault("POSTGRES_HOST", "/tmp/pg_sock_m3d7")
os.environ.setdefault("POSTGRES_PORT", "5439")
os.environ.setdefault("POSTGRES_DB", "ai_hub_test_m3d7")
os.environ.setdefault("POSTGRES_USER", "test_user_m3d7")
os.environ.setdefault("POSTGRES_PASSWORD", "test_password_m3d7")
os.environ.setdefault("MCP_ACCESS_TOKEN", "test-only-mcp-token")
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from app.core.authorization import AuthorizationContext, Capability
from app.core.context_assembly import assemble_canonical_context
from app.core.db import get_db_connection
from app.main import app
from app.routers import documents as documents_router

TEST_UPLOAD_DIR = Path(tempfile.mkdtemp(prefix="ai_hub_m3d7_uploads_"))
os.environ["DOCUMENTS_UPLOAD_DIR"] = str(TEST_UPLOAD_DIR)
documents_router.DOCUMENTS_UPLOAD_DIR = TEST_UPLOAD_DIR

TEST_COMMIT_SHA = "fd9742cf9cf2940654833fcf8c3c8d951adeb3e1"
UNIT_VECTOR_384 = "[" + ",".join(["1.0"] + ["0.0"] * 383) + "]"


def github_source(rel_path: str) -> str:
    return f"https://github.com/CHUNGJINWOO/learning-portfolio/blob/{TEST_COMMIT_SHA}/{rel_path}"


def verify_safety_guard():
    """Verify that the active connection is strictly to the disposable test DB."""
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT current_database(), current_user, current_setting('server_version_num')::int;"
        ).fetchone()
        db_name, user, ver_num = row[0], row[1], row[2]
        if db_name != "ai_hub_test_m3d7":
            raise RuntimeError(
                f"SAFETY GUARD TRIGGERED: Expected database 'ai_hub_test_m3d7', but connected to '{db_name}'!"
            )
        if ver_num // 10000 != 17:
            raise RuntimeError(
                f"SAFETY GUARD TRIGGERED: Expected PostgreSQL 17, but server version num is {ver_num}!"
            )
def is_live_pg17_available() -> bool:
    try:
        with get_db_connection() as conn:
            row = conn.execute("SELECT current_database();").fetchone()
            return row is not None and row[0] == "ai_hub_test_m3d7"
    except Exception:
        return False


@unittest.skipUnless(
    is_live_pg17_available(),
    "Live PostgreSQL 17 test instance ('ai_hub_test_m3d7') is not running or accessible",
)
class LivePostgres17IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        verify_safety_guard()
        cls.client = TestClient(app)
        os.environ["DOCUMENTS_UPLOAD_DIR"] = str(TEST_UPLOAD_DIR)
        TEST_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEST_UPLOAD_DIR, ignore_errors=True)

    def setUp(self):
        verify_safety_guard()
        os.environ["DOCUMENTS_UPLOAD_DIR"] = str(TEST_UPLOAD_DIR)
        TEST_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        # Clean test tables between tests to ensure test isolation
        with get_db_connection() as conn:
            conn.execute("DELETE FROM document_chunks;")
            conn.execute("DELETE FROM documents;")
            conn.execute("DELETE FROM memories;")
            conn.execute("DELETE FROM projects;")
            conn.commit()

    def _create_project(self, name: str = "Test Project", slug: str = "test-project", status: str = "active") -> int:
        with get_db_connection() as conn:
            row = conn.execute(
                """
                INSERT INTO projects (name, slug, description, status)
                VALUES (%s, %s, 'Test description', %s)
                RETURNING id;
                """,
                (name, slug, status),
            ).fetchone()
            conn.commit()
            return row[0]

    def _client_with_auth(
        self,
        allowed_projects: set[int] | None = None,
        allow_global_read: bool = False,
        allow_write: bool = True,
        capabilities: set[Capability] | None = None,
    ) -> TestClient:
        ctx = AuthorizationContext(
            identity="m3d7-tester",
            allowed_project_ids=frozenset(allowed_projects or set()),
            allow_global_read=allow_global_read,
            allow_write=allow_write,
            capabilities=frozenset(capabilities or set()),
        )
        client = TestClient(app)
        client.app_state["authorization_context"] = ctx
        return client

    # =========================================================================
    # Group A: Document Collection Tests (Section 5.A 1-7)
    # =========================================================================

    def test_A1_initial_upload_and_db_persistence(self):
        """A1: First upload successfully persists document and chunks in PostgreSQL."""
        project_id = self._create_project("Project A1", "proj-a1")
        client = self._client_with_auth(allowed_projects={project_id})

        content = b"# Architecture Overview\nThis is a real PostgreSQL integration test.\n"
        sha256 = hashlib.sha256(content).hexdigest()
        rel_path = "docs/architecture.md"

        response = client.post(
            f"/documents/upload?project_id={project_id}",
            data={
                "project_id": str(project_id),
                "title": "Architecture Overview",
                "relative_path": rel_path,
                "source": github_source(rel_path),
                "commit_sha": TEST_COMMIT_SHA,
                "expected_sha256": sha256,
            },
            files={"file": ("architecture.md", io.BytesIO(content), "text/markdown")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["status"], "created")
        self.assertEqual(data["relative_path"], rel_path)
        self.assertEqual(data["upload_integrity"], "client_sha256_match")
        self.assertEqual(data["provenance_assertion"], "client_git_commit")
        self.assertFalse(data["server_git_verified"])

        doc_id = data["document_id"]

        # Verify DB state directly in PostgreSQL 17
        with get_db_connection() as conn:
            doc = conn.execute(
                """
                SELECT id, project_id, title, relative_path, sha256, status, source
                FROM documents WHERE id = %s;
                """,
                (doc_id,),
            ).fetchone()
            self.assertIsNotNone(doc)
            self.assertEqual(doc[0], doc_id)
            self.assertEqual(doc[1], project_id)
            self.assertEqual(doc[2], "Architecture Overview")
            self.assertEqual(doc[3], rel_path)
            self.assertEqual(doc[4], sha256)
            self.assertEqual(doc[5], "active")

            chunks = conn.execute(
                "SELECT count(*) FROM document_chunks WHERE document_id = %s;",
                (doc_id,),
            ).fetchone()[0]
            self.assertGreater(chunks, 0)

    def test_A2_same_path_same_hash_duplicate_no_create(self):
        """A2: Same path & same hash returns duplicate status without creating DB rows."""
        project_id = self._create_project("Project A2", "proj-a2")
        client = self._client_with_auth(allowed_projects={project_id})

        content = b"# Deduplication Target\nConsistent content."
        sha256 = hashlib.sha256(content).hexdigest()
        rel_path = "docs/dedup.md"

        # Upload 1
        resp1 = client.post(
            f"/documents/upload?project_id={project_id}",
            data={
                "project_id": str(project_id),
                "title": "Doc 1",
                "relative_path": rel_path,
                "source": github_source(rel_path),
                "commit_sha": TEST_COMMIT_SHA,
            },
            files={"file": ("dedup.md", io.BytesIO(content), "text/markdown")},
        )
        self.assertEqual(resp1.status_code, 200, resp1.text)
        doc1_id = resp1.json()["document_id"]
        self.assertEqual(resp1.json()["status"], "created")

        # Upload 2 (duplicate)
        resp2 = client.post(
            f"/documents/upload?project_id={project_id}",
            data={
                "project_id": str(project_id),
                "title": "Doc 1 Re-upload",
                "relative_path": rel_path,
                "source": github_source(rel_path),
                "commit_sha": TEST_COMMIT_SHA,
            },
            files={"file": ("dedup.md", io.BytesIO(content), "text/markdown")},
        )
        self.assertEqual(resp2.status_code, 200, resp2.text)
        self.assertEqual(resp2.json()["status"], "duplicate")
        self.assertEqual(resp2.json()["document_id"], doc1_id)

        # Verify no second row created in PostgreSQL
        with get_db_connection() as conn:
            count = conn.execute(
                "SELECT count(*) FROM documents WHERE project_id = %s AND relative_path = %s;",
                (project_id, rel_path),
            ).fetchone()[0]
            self.assertEqual(count, 1)

    def test_A3_same_path_different_hash_conflict(self):
        """A3: Same path with different content returns 409 Conflict, preserving existing."""
        project_id = self._create_project("Project A3", "proj-a3")
        client = self._client_with_auth(allowed_projects={project_id})

        content_v1 = b"# Version 1\nOriginal content."
        sha256_v1 = hashlib.sha256(content_v1).hexdigest()
        content_v2 = b"# Version 2\nAltered content."
        rel_path = "docs/versioned.md"

        # Upload v1
        resp1 = client.post(
            f"/documents/upload?project_id={project_id}",
            data={
                "project_id": str(project_id),
                "title": "Version 1",
                "relative_path": rel_path,
                "source": github_source(rel_path),
                "commit_sha": TEST_COMMIT_SHA,
            },
            files={"file": ("versioned.md", io.BytesIO(content_v1), "text/markdown")},
        )
        self.assertEqual(resp1.status_code, 200, resp1.text)
        doc1_id = resp1.json()["document_id"]

        # Upload v2 with same path
        resp2 = client.post(
            f"/documents/upload?project_id={project_id}",
            data={
                "project_id": str(project_id),
                "title": "Version 2",
                "relative_path": rel_path,
                "source": github_source(rel_path),
                "commit_sha": TEST_COMMIT_SHA,
            },
            files={"file": ("versioned.md", io.BytesIO(content_v2), "text/markdown")},
        )
        self.assertEqual(resp2.status_code, 409)
        self.assertIn("different content", resp2.json()["detail"])

        # Check DB: original doc is untouched
        with get_db_connection() as conn:
            doc = conn.execute(
                "SELECT sha256 FROM documents WHERE id = %s;", (doc1_id,)
            ).fetchone()
            self.assertEqual(doc[0], sha256_v1)

    def test_A4_different_paths_same_filename(self):
        """A4: Same filename at different relative paths are distinguished independently."""
        project_id = self._create_project("Project A4", "proj-a4")
        client = self._client_with_auth(allowed_projects={project_id})

        content = b"# Common Readme\nShared text."
        path1 = "modules/module_a/README.md"
        path2 = "modules/module_b/README.md"

        resp1 = client.post(
            f"/documents/upload?project_id={project_id}",
            data={
                "project_id": str(project_id),
                "title": "Module A Readme",
                "relative_path": path1,
                "source": github_source(path1),
                "commit_sha": TEST_COMMIT_SHA,
            },
            files={"file": ("README.md", io.BytesIO(content), "text/markdown")},
        )
        resp2 = client.post(
            f"/documents/upload?project_id={project_id}",
            data={
                "project_id": str(project_id),
                "title": "Module B Readme",
                "relative_path": path2,
                "source": github_source(path2),
                "commit_sha": TEST_COMMIT_SHA,
            },
            files={"file": ("README.md", io.BytesIO(content), "text/markdown")},
        )

        self.assertEqual(resp1.status_code, 200, resp1.text)
        self.assertEqual(resp2.status_code, 200, resp2.text)
        id1 = resp1.json()["document_id"]
        id2 = resp2.json()["document_id"]
        self.assertNotEqual(id1, id2)

        with get_db_connection() as conn:
            paths = conn.execute(
                "SELECT relative_path FROM documents WHERE project_id = %s ORDER BY id;",
                (project_id,),
            ).fetchall()
            self.assertEqual([p[0] for p in paths], [path1, path2])

    def test_A5_legacy_upload_duplicate_filename(self):
        """A5: Legacy upload without relative_path always creates a new document."""
        project_id = self._create_project("Project A5", "proj-a5")
        client = self._client_with_auth(allowed_projects={project_id})

        content = b"<package><name>ros2_node</name></package>"

        resp1 = client.post(
            f"/documents/upload?project_id={project_id}",
            data={"project_id": str(project_id), "title": "Package XML 1"},
            files={"file": ("package.xml", io.BytesIO(content), "application/xml")},
        )
        resp2 = client.post(
            f"/documents/upload?project_id={project_id}",
            data={"project_id": str(project_id), "title": "Package XML 2"},
            files={"file": ("package.xml", io.BytesIO(content), "application/xml")},
        )

        self.assertEqual(resp1.status_code, 200, resp1.text)
        self.assertEqual(resp2.status_code, 200, resp2.text)
        id1 = resp1.json()["document_id"]
        id2 = resp2.json()["document_id"]
        self.assertNotEqual(id1, id2)

        with get_db_connection() as conn:
            count = conn.execute(
                "SELECT count(*) FROM documents WHERE project_id = %s AND filename = 'package.xml';",
                (project_id,),
            ).fetchone()[0]
            self.assertEqual(count, 2)

    def test_A6_real_chunking_and_pgvector_embedding_insert(self):
        """A6: Real text chunking and pgvector embedding storage in PostgreSQL 17."""
        project_id = self._create_project("Project A6", "proj-a6")
        client = self._client_with_auth(allowed_projects={project_id})

        content = b"# Topic 1\nFirst paragraph content.\n\n# Topic 2\nSecond paragraph content.\n"
        rel_path = "docs/chunked.md"
        resp = client.post(
            f"/documents/upload?project_id={project_id}",
            data={
                "project_id": str(project_id),
                "title": "Chunked Doc",
                "relative_path": rel_path,
                "source": github_source(rel_path),
                "commit_sha": TEST_COMMIT_SHA,
            },
            files={"file": ("chunked.md", io.BytesIO(content), "text/markdown")},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        doc_id = resp.json()["document_id"]

        with get_db_connection() as conn:
            chunks = conn.execute(
                "SELECT chunk_index, content, embedding FROM document_chunks WHERE document_id = %s ORDER BY chunk_index;",
                (doc_id,),
            ).fetchall()
            self.assertGreater(len(chunks), 0)
            for c in chunks:
                self.assertIsNotNone(c[2])  # pgvector embedding not null

            # Execute real pgvector distance query with normalized unit vector
            nearest = conn.execute(
                """
                SELECT chunk_index, content, embedding <=> %s AS distance
                FROM document_chunks
                WHERE document_id = %s
                ORDER BY distance
                LIMIT 1;
                """,
                (UNIT_VECTOR_384, doc_id),
            ).fetchone()
            self.assertIsNotNone(nearest)
            self.assertIsInstance(nearest[2], float)

    def test_A7_file_size_and_authorization(self):
        """A7: Unauthorized uploads return 403, oversized uploads return 413."""
        project_id = self._create_project("Project A7", "proj-a7")

        # 1. Read-only client attempt
        ro_client = self._client_with_auth(allowed_projects={project_id}, allow_write=False)
        resp_ro = ro_client.post(
            f"/documents/upload?project_id={project_id}",
            data={"project_id": str(project_id), "title": "Forbidden Doc"},
            files={"file": ("doc.txt", io.BytesIO(b"content"), "text/plain")},
        )
        self.assertEqual(resp_ro.status_code, 403)

        # 2. Oversized file attempt (temporarily set MAX_UPLOAD_SIZE_BYTES to 100 bytes)
        client = self._client_with_auth(allowed_projects={project_id})
        with patch.object(documents_router, "MAX_UPLOAD_SIZE_BYTES", 100):
            big_content = b"X" * 200
            resp_big = client.post(
                f"/documents/upload?project_id={project_id}",
                data={"project_id": str(project_id), "title": "Big Doc"},
                files={"file": ("big.txt", io.BytesIO(big_content), "text/plain")},
            )
            self.assertEqual(resp_big.status_code, 413)

    # =========================================================================
    # Group B: Concurrency and File Safety Tests (Section 5.B 1-7)
    # =========================================================================

    def test_B1_concurrent_upload_race_condition(self):
        """B1: Concurrent uploads for the same relative_path serialize safely without duplicate rows."""
        project_id = self._create_project("Project B1", "proj-b1")
        content = b"# Concurrent Safety Test\nContent to upload concurrently."
        rel_path = "docs/concurrent.md"

        def do_upload(thread_idx: int):
            client = self._client_with_auth(allowed_projects={project_id})
            return client.post(
                f"/documents/upload?project_id={project_id}",
                data={
                    "project_id": str(project_id),
                    "title": f"Concurrent {thread_idx}",
                    "relative_path": rel_path,
                    "source": github_source(rel_path),
                    "commit_sha": TEST_COMMIT_SHA,
                },
                files={"file": ("concurrent.md", io.BytesIO(content), "text/markdown")},
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(do_upload, i) for i in range(3)]
            results = [f.result() for f in futures]

        for r in results:
            self.assertEqual(r.status_code, 200, r.text)

        statuses = [r.json()["status"] for r in results]
        self.assertIn("created", statuses)
        self.assertEqual(statuses.count("created"), 1)
        self.assertEqual(statuses.count("duplicate"), 2)

        with get_db_connection() as conn:
            count = conn.execute(
                "SELECT count(*) FROM documents WHERE project_id = %s AND relative_path = %s;",
                (project_id, rel_path),
            ).fetchone()[0]
            self.assertEqual(count, 1)

    def test_B2_advisory_lock_wait_and_release(self):
        """B2: Advisory lock serializes execution and releases automatically upon commit/rollback."""
        project_id = self._create_project("Project B2", "proj-b2")
        rel_path = "docs/locked.md"

        lock_hash = hashlib.sha256(f"{project_id}:{rel_path}".encode("utf-8")).hexdigest()[:15]
        lock_key = int(lock_hash, 16) & 0x7FFFFFFFFFFFFFFF

        lock_acquired_time = None
        lock_released_time = None

        def hold_lock():
            nonlocal lock_acquired_time, lock_released_time
            with get_db_connection() as conn:
                conn.execute("SELECT pg_advisory_xact_lock(%s);", (lock_key,))
                lock_acquired_time = time.time()
                time.sleep(0.4)
                conn.commit()
                lock_released_time = time.time()

        t = concurrent.futures.ThreadPoolExecutor(max_workers=1).submit(hold_lock)
        time.sleep(0.1)  # Ensure holder acquired lock

        client = self._client_with_auth(allowed_projects={project_id})
        start_upload = time.time()
        resp = client.post(
            f"/documents/upload?project_id={project_id}",
            data={
                "project_id": str(project_id),
                "title": "Locked Doc",
                "relative_path": rel_path,
                "source": github_source(rel_path),
                "commit_sha": TEST_COMMIT_SHA,
            },
            files={"file": ("locked.md", io.BytesIO(b"# Content"), "text/markdown")},
        )
        end_upload = time.time()
        t.result()

        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertGreaterEqual(end_upload, lock_released_time)

    def test_B3_db_failure_atomic_rollback(self):
        """B3: An exception during chunk/document insertion rolls back completely."""
        project_id = self._create_project("Project B3", "proj-b3")
        client = self._client_with_auth(allowed_projects={project_id})
        rel_path = "docs/rollback.md"

        with patch("app.core.embedding.model.encode", side_effect=RuntimeError("Embedding model failure")):
            resp = client.post(
                f"/documents/upload?project_id={project_id}",
                data={
                    "project_id": str(project_id),
                    "title": "Rollback Test",
                    "relative_path": rel_path,
                    "source": github_source(rel_path),
                    "commit_sha": TEST_COMMIT_SHA,
                },
                files={"file": ("rollback.md", io.BytesIO(b"# Rollback Content"), "text/markdown")},
            )
            self.assertEqual(resp.status_code, 500)

        # Verify nothing committed to DB
        with get_db_connection() as conn:
            docs = conn.execute("SELECT count(*) FROM documents WHERE project_id = %s;", (project_id,)).fetchone()[0]
            chunks = conn.execute("SELECT count(*) FROM document_chunks;").fetchone()[0]
            self.assertEqual(docs, 0)
            self.assertEqual(chunks, 0)

    def test_B4_failed_upload_does_not_delete_committed_files(self):
        """B4: Failure of request 2 does not delete or truncate request 1's committed file."""
        project_id = self._create_project("Project B4", "proj-b4")
        client = self._client_with_auth(allowed_projects={project_id})
        rel_path_1 = "docs/stable.md"
        rel_path_2 = "other/stable.md"

        # Request 1: succeeds
        content_1 = b"# Stable File\nCommitted content."
        resp1 = client.post(
            f"/documents/upload?project_id={project_id}",
            data={
                "project_id": str(project_id),
                "title": "Stable Doc",
                "relative_path": rel_path_1,
                "source": github_source(rel_path_1),
                "commit_sha": TEST_COMMIT_SHA,
            },
            files={"file": ("stable.md", io.BytesIO(content_1), "text/markdown")},
        )
        self.assertEqual(resp1.status_code, 200, resp1.text)

        # Identify committed file path
        sha1 = hashlib.sha256(content_1).hexdigest()
        upload_root = Path(os.environ.get("DOCUMENTS_UPLOAD_DIR", "/data/documents"))
        committed_file = upload_root / f"{sha1}_stable.md"
        self.assertTrue(committed_file.exists())
        self.assertEqual(committed_file.read_bytes(), content_1)

        # Request 2: same filename, different path, fails during extraction
        with patch("app.routers.documents.extract_text", side_effect=ValueError("Corrupted document")):
            resp2 = client.post(
                f"/documents/upload?project_id={project_id}",
                data={
                    "project_id": str(project_id),
                    "title": "Failing Doc",
                    "relative_path": rel_path_2,
                    "source": github_source(rel_path_2),
                    "commit_sha": TEST_COMMIT_SHA,
                },
                files={"file": ("stable.md", io.BytesIO(b"Different corrupted bytes"), "text/markdown")},
            )
            self.assertEqual(resp2.status_code, 400)

        # Verify committed file 1 is still 100% intact!
        self.assertTrue(committed_file.exists())
        self.assertEqual(committed_file.read_bytes(), content_1)

    def test_B5_same_hash_different_paths_storage_safety(self):
        """B5: Identical contents uploaded to different paths share storage safely without error."""
        project_id = self._create_project("Project B5", "proj-b5")
        client = self._client_with_auth(allowed_projects={project_id})

        content = b"# Shared Content\nSame bytes across two paths."
        sha = hashlib.sha256(content).hexdigest()
        path1 = "area_a/doc.md"
        path2 = "area_b/doc.md"

        resp1 = client.post(
            f"/documents/upload?project_id={project_id}",
            data={
                "project_id": str(project_id),
                "title": "Doc Path 1",
                "relative_path": path1,
                "source": github_source(path1),
                "commit_sha": TEST_COMMIT_SHA,
            },
            files={"file": ("doc.md", io.BytesIO(content), "text/markdown")},
        )
        resp2 = client.post(
            f"/documents/upload?project_id={project_id}",
            data={
                "project_id": str(project_id),
                "title": "Doc Path 2",
                "relative_path": path2,
                "source": github_source(path2),
                "commit_sha": TEST_COMMIT_SHA,
            },
            files={"file": ("doc.md", io.BytesIO(content), "text/markdown")},
        )

        self.assertEqual(resp1.status_code, 200, resp1.text)
        self.assertEqual(resp2.status_code, 200, resp2.text)
        self.assertNotEqual(resp1.json()["document_id"], resp2.json()["document_id"])

        upload_root = Path(os.environ.get("DOCUMENTS_UPLOAD_DIR", "/data/documents"))
        target_file = upload_root / f"{sha}_doc.md"
        self.assertTrue(target_file.exists())
        self.assertEqual(target_file.read_bytes(), content)

    def test_B6_final_storage_overwriting_prevention(self):
        """B6: Duplicate upload does not corrupt or truncate the committed file."""
        project_id = self._create_project("Project B6", "proj-b6")
        client = self._client_with_auth(allowed_projects={project_id})

        content = b"# Storage Invariance\nOriginal bytes."
        sha = hashlib.sha256(content).hexdigest()
        rel_path = "docs/invariant.md"

        # Upload twice
        for _ in range(2):
            resp = client.post(
                f"/documents/upload?project_id={project_id}",
                data={
                    "project_id": str(project_id),
                    "title": "Invariant Doc",
                    "relative_path": rel_path,
                    "source": github_source(rel_path),
                    "commit_sha": TEST_COMMIT_SHA,
                },
                files={"file": ("invariant.md", io.BytesIO(content), "text/markdown")},
            )
            self.assertEqual(resp.status_code, 200, resp.text)

        upload_root = Path(os.environ.get("DOCUMENTS_UPLOAD_DIR", "/data/documents"))
        target_file = upload_root / f"{sha}_invariant.md"
        self.assertTrue(target_file.exists())
        self.assertEqual(target_file.read_bytes(), content)

    def test_B7_temp_file_cleanup_on_error(self):
        """B7: Failed uploads clean up isolated temp files (tmp_*) completely."""
        project_id = self._create_project("Project B7", "proj-b7")
        client = self._client_with_auth(allowed_projects={project_id})
        rel_path = "docs/tmp_clean.md"

        with patch("app.routers.documents.extract_text", side_effect=ValueError("Forced parse error")):
            resp = client.post(
                f"/documents/upload?project_id={project_id}",
                data={
                    "project_id": str(project_id),
                    "title": "Temp Cleanup Doc",
                    "relative_path": rel_path,
                    "source": github_source(rel_path),
                    "commit_sha": TEST_COMMIT_SHA,
                },
                files={"file": ("tmp_clean.md", io.BytesIO(b"# Corrupt data"), "text/markdown")},
            )
            self.assertEqual(resp.status_code, 400)

        # Verify no orphan tmp_* files remained
        upload_root = Path(os.environ.get("DOCUMENTS_UPLOAD_DIR", "/data/documents"))
        remaining_tmp = list(upload_root.glob("tmp_*"))
        self.assertEqual(remaining_tmp, [])

    # =========================================================================
    # Group C: Search and Permissions Tests (Section 5.C 1-9)
    # =========================================================================

    def test_C1_active_project_search(self):
        """C1: Documents and memories in active projects are discoverable in search."""
        proj_id = self._create_project("Active Project", "active-proj", status="active")

        # Insert document and memory with normalized unit vectors
        with get_db_connection() as conn:
            doc_id = conn.execute(
                """
                INSERT INTO documents (project_id, title, filename, status)
                VALUES (%s, 'Active Guide', 'active.md', 'active')
                RETURNING id;
                """,
                (proj_id,),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO document_chunks (document_id, chunk_index, content, embedding)
                VALUES (%s, 0, 'Active project guide content about navigation', %s);
                """,
                (doc_id, UNIT_VECTOR_384),
            )
            conn.execute(
                """
                INSERT INTO memories (content, project_id, embedding)
                VALUES ('Active project memory regarding waypoint navigation', %s, %s);
                """,
                (proj_id, UNIT_VECTOR_384),
            )
            conn.commit()

        client = self._client_with_auth(allowed_projects={proj_id})
        res_docs = client.get(f"/documents/search?q=navigation&project_id={proj_id}")
        res_mems = client.get(f"/memories/search?q=waypoint&project_id={proj_id}")

        self.assertEqual(res_docs.status_code, 200, res_docs.text)
        self.assertEqual(res_mems.status_code, 200, res_mems.text)
        self.assertGreater(len(res_docs.json()["results"]), 0)
        self.assertGreater(len(res_mems.json()["results"]), 0)

    def test_C2_completed_project_search(self):
        """C2: Documents and memories in completed projects are discoverable in search."""
        proj_id = self._create_project("Completed Project", "completed-proj", status="completed")

        with get_db_connection() as conn:
            doc_id = conn.execute(
                """
                INSERT INTO documents (project_id, title, filename, status)
                VALUES (%s, 'Completed Report', 'completed.md', 'active')
                RETURNING id;
                """,
                (proj_id,),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO document_chunks (document_id, chunk_index, content, embedding)
                VALUES (%s, 0, 'Completed project summary about lidar calibration', %s);
                """,
                (doc_id, UNIT_VECTOR_384),
            )
            conn.execute(
                """
                INSERT INTO memories (content, project_id, embedding)
                VALUES ('Completed memory about sensor offsets', %s, %s);
                """,
                (proj_id, UNIT_VECTOR_384),
            )
            conn.commit()

        client = self._client_with_auth(allowed_projects={proj_id})
        res_docs = client.get(f"/documents/search?q=lidar&project_id={proj_id}")
        res_mems = client.get(f"/memories/search?q=sensor&project_id={proj_id}")

        self.assertEqual(res_docs.status_code, 200, res_docs.text)
        self.assertEqual(res_mems.status_code, 200, res_mems.text)
        self.assertGreater(len(res_docs.json()["results"]), 0)
        self.assertGreater(len(res_mems.json()["results"]), 0)

    def test_C3_archived_project_search_excluded(self):
        """C3: Documents and memories in archived projects are strictly excluded from search."""
        proj_id = self._create_project("Archived Project", "archived-proj", status="archived")

        with get_db_connection() as conn:
            doc_id = conn.execute(
                """
                INSERT INTO documents (project_id, title, filename, status)
                VALUES (%s, 'Archived Guide', 'archived.md', 'active')
                RETURNING id;
                """,
                (proj_id,),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO document_chunks (document_id, chunk_index, content, embedding)
                VALUES (%s, 0, 'Archived secret navigation content', %s);
                """,
                (doc_id, UNIT_VECTOR_384),
            )
            conn.execute(
                """
                INSERT INTO memories (content, project_id, embedding)
                VALUES ('Archived memory content about deprecated hardware', %s, %s);
                """,
                (proj_id, UNIT_VECTOR_384),
            )
            conn.commit()

        client = self._client_with_auth(allowed_projects={proj_id})
        res_docs = client.get(f"/documents/search?q=secret&project_id={proj_id}")
        res_mems = client.get(f"/memories/search?q=deprecated&project_id={proj_id}")

        self.assertEqual(res_docs.status_code, 200, res_docs.text)
        self.assertEqual(res_mems.status_code, 200, res_mems.text)
        self.assertEqual(len(res_docs.json()["results"]), 0)
        self.assertEqual(len(res_mems.json()["results"]), 0)

    def test_C4_archived_document_search_excluded(self):
        """C4: Documents with status='archived' are excluded even if project is active."""
        proj_id = self._create_project("Active Proj C4", "active-c4", status="active")

        with get_db_connection() as conn:
            doc_id = conn.execute(
                """
                INSERT INTO documents (project_id, title, filename, status)
                VALUES (%s, 'Archived Doc in Active Proj', 'doc_c4.md', 'archived')
                RETURNING id;
                """,
                (proj_id,),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO document_chunks (document_id, chunk_index, content, embedding)
                VALUES (%s, 0, 'Unique text in archived doc search target', %s);
                """,
                (doc_id, UNIT_VECTOR_384),
            )
            conn.commit()

        client = self._client_with_auth(allowed_projects={proj_id})
        res_docs = client.get(f"/documents/search?q=unique&project_id={proj_id}")
        self.assertEqual(res_docs.status_code, 200, res_docs.text)
        self.assertEqual(len(res_docs.json()["results"]), 0)

    def test_C5_global_search_across_projects(self):
        """C5: Global search returns items from active/completed projects and excludes archived."""
        p_act = self._create_project("Active Proj C5", "act-c5", status="active")
        p_comp = self._create_project("Completed Proj C5", "comp-c5", status="completed")
        p_arch = self._create_project("Archived Proj C5", "arch-c5", status="archived")

        with get_db_connection() as conn:
            # Active
            d1 = conn.execute("INSERT INTO documents (project_id, title, status) VALUES (%s, 'Doc 1', 'active') RETURNING id;", (p_act,)).fetchone()[0]
            conn.execute("INSERT INTO document_chunks (document_id, chunk_index, content, embedding) VALUES (%s, 0, 'Global benchmark act', %s);", (d1, UNIT_VECTOR_384))

            # Completed
            d2 = conn.execute("INSERT INTO documents (project_id, title, status) VALUES (%s, 'Doc 2', 'active') RETURNING id;", (p_comp,)).fetchone()[0]
            conn.execute("INSERT INTO document_chunks (document_id, chunk_index, content, embedding) VALUES (%s, 0, 'Global benchmark comp', %s);", (d2, UNIT_VECTOR_384))

            # Archived
            d3 = conn.execute("INSERT INTO documents (project_id, title, status) VALUES (%s, 'Doc 3', 'active') RETURNING id;", (p_arch,)).fetchone()[0]
            conn.execute("INSERT INTO document_chunks (document_id, chunk_index, content, embedding) VALUES (%s, 0, 'Global benchmark arch', %s);", (d3, UNIT_VECTOR_384))
            conn.commit()

        # Global search with PROJECT_READ_ALL capability
        client = self._client_with_auth(
            allow_global_read=True,
            capabilities={Capability.PROJECT_READ_ALL, Capability.RESOURCE_READ_GLOBAL},
        )
        res = client.get("/documents/search?q=benchmark")
        self.assertEqual(res.status_code, 200, res.text)
        items = res.json()["results"]
        returned_doc_ids = {item["document_id"] for item in items}
        self.assertIn(d1, returned_doc_ids)
        self.assertIn(d2, returned_doc_ids)
        self.assertNotIn(d3, returned_doc_ids)

    def test_C6_restricted_project_read_permission(self):
        """C6: Unauthorized project access returns 403 Forbidden."""
        p1 = self._create_project("Project 1", "proj-1")
        p2 = self._create_project("Project 2", "proj-2")

        client = self._client_with_auth(allowed_projects={p1})

        # Accessing p2 is forbidden
        res = client.get(f"/documents/search?q=test&project_id={p2}")
        self.assertEqual(res.status_code, 403)

    def test_C7_documents_search_and_memories_search_endpoints(self):
        """C7: FastAPI search endpoints execute native PostgreSQL pgvector queries cleanly."""
        proj_id = self._create_project("Search Endpoints Proj", "search-endpoints")

        with get_db_connection() as conn:
            d = conn.execute("INSERT INTO documents (project_id, title, status) VALUES (%s, 'ROS2 Title', 'active') RETURNING id;", (proj_id,)).fetchone()[0]
            conn.execute("INSERT INTO document_chunks (document_id, chunk_index, content, embedding) VALUES (%s, 0, 'Publisher subscriber pattern', %s);", (d, UNIT_VECTOR_384))
            conn.execute("INSERT INTO memories (content, project_id, embedding) VALUES ('QoS reliability memory', %s, %s);", (proj_id, UNIT_VECTOR_384))
            conn.commit()

        client = self._client_with_auth(allowed_projects={proj_id})
        res_d = client.get(f"/documents/search?q=subscriber&project_id={proj_id}")
        res_m = client.get(f"/memories/search?q=reliability&project_id={proj_id}")

        self.assertEqual(res_d.status_code, 200, res_d.text)
        self.assertEqual(res_m.status_code, 200, res_m.text)
        docs_results = res_d.json()["results"]
        mems_results = res_m.json()["results"]
        self.assertTrue(any("Publisher" in item.get("snippet", "") or "Publisher" in item.get("content", "") for item in docs_results))
        self.assertTrue(any("QoS" in item.get("content", "") for item in mems_results))

    def test_C8_search_context_and_get_context_provenance(self):
        """C8: Context assembly queries live PostgreSQL and passes relative_path, source, commit_sha."""
        proj_id = self._create_project("Context Assembly Proj", "ctx-proj")
        rel_path = "limo/nav.md"
        src_url = github_source(rel_path)

        with get_db_connection() as conn:
            d = conn.execute(
                """
                INSERT INTO documents (project_id, title, status, relative_path, source)
                VALUES (%s, 'LIMO Nav', 'active', %s, %s)
                RETURNING id;
                """,
                (proj_id, rel_path, src_url),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO document_chunks (document_id, chunk_index, content, embedding)
                VALUES (%s, 0, 'Autonomous navigation parameters for LIMO robot', %s);
                """,
                (d, UNIT_VECTOR_384),
            )
            conn.commit()

        ctx = assemble_canonical_context(
            query="autonomous navigation",
            project_id=proj_id,
            limit=5,
        )
        self.assertIsNotNone(ctx)
        self.assertGreater(len(ctx.sources), 0)
        first_source = ctx.sources[0]
        self.assertEqual(first_source.relative_path, rel_path)
        self.assertEqual(first_source.source, src_url)

    def test_C9_metadata_retrieval_endpoint(self):
        """C9: GET /documents/{id} returns relative_path, source, commit_sha, and integrity."""
        proj_id = self._create_project("Meta Retrieval Proj", "meta-proj")
        client = self._client_with_auth(allowed_projects={proj_id})

        content = b"# Metadata Inspection\nInspecting fields."
        rel_path = "inspection/meta.md"
        resp = client.post(
            f"/documents/upload?project_id={proj_id}",
            data={
                "project_id": str(proj_id),
                "title": "Meta Doc",
                "relative_path": rel_path,
                "source": github_source(rel_path),
                "commit_sha": TEST_COMMIT_SHA,
            },
            files={"file": ("meta.md", io.BytesIO(content), "text/markdown")},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        doc_id = resp.json()["document_id"]

        get_resp = client.get(f"/documents/{doc_id}")
        self.assertEqual(get_resp.status_code, 200, get_resp.text)
        doc_info = get_resp.json()
        self.assertEqual(doc_info["relative_path"], rel_path)
        self.assertEqual(doc_info["source"], github_source(rel_path))
        self.assertIn("sha256", doc_info)

    def test_C10_list_documents_provenance_metadata(self):
        """C10: GET /documents returns relative_path and sha256 for both provenance and legacy docs."""
        proj_id = self._create_project("List Meta Proj", "list-meta-proj")
        client = self._client_with_auth(allowed_projects={proj_id})

        # 1. Upload document with provenance
        rel_path = "src/module.py"
        content_prov = b"print('provenance')"
        resp_prov = client.post(
            f"/documents/upload?project_id={proj_id}",
            data={
                "project_id": str(proj_id),
                "title": "Provenance Doc",
                "relative_path": rel_path,
                "source": github_source(rel_path),
                "commit_sha": TEST_COMMIT_SHA,
            },
            files={"file": ("module.py", io.BytesIO(content_prov), "text/x-python")},
        )
        self.assertEqual(resp_prov.status_code, 200, resp_prov.text)
        prov_doc_id = resp_prov.json()["document_id"]
        prov_sha = resp_prov.json()["sha256"]

        # 2. Upload legacy document without relative_path
        content_legacy = b"legacy document text"
        resp_legacy = client.post(
            f"/documents/upload?project_id={proj_id}",
            data={"project_id": str(proj_id), "title": "Legacy Doc"},
            files={"file": ("legacy.txt", io.BytesIO(content_legacy), "text/plain")},
        )
        self.assertEqual(resp_legacy.status_code, 200, resp_legacy.text)
        legacy_doc_id = resp_legacy.json()["document_id"]
        legacy_sha = resp_legacy.json()["sha256"]

        # 3. Query GET /documents?project_id={proj_id}
        list_resp = client.get(f"/documents?project_id={proj_id}")
        self.assertEqual(list_resp.status_code, 200, list_resp.text)
        data = list_resp.json()
        self.assertEqual(data["count"], 2)
        docs_by_id = {d["id"]: d for d in data["documents"]}

        # Check provenance document
        prov_doc = docs_by_id[prov_doc_id]
        self.assertEqual(prov_doc["relative_path"], rel_path)
        self.assertEqual(prov_doc["sha256"], prov_sha)

        # Check legacy document (relative_path defaults to safe_name, sha256 computed)
        legacy_doc = docs_by_id[legacy_doc_id]
        self.assertEqual(legacy_doc["relative_path"], "legacy.txt")
        self.assertEqual(legacy_doc["sha256"], legacy_sha)

        # Check field consistency between detail and list
        detail_resp = client.get(f"/documents/{prov_doc_id}")
        self.assertEqual(detail_resp.status_code, 200)
        detail_doc = detail_resp.json()
        self.assertEqual(set(prov_doc.keys()), set(detail_doc.keys()))


if __name__ == "__main__":
    unittest.main()
