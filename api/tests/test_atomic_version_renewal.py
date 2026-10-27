"""
M3-F-E3-FIX: Comprehensive Test Suite for CAS and Active Document Unique Constraints.

Verifies all failure, concurrency, governance, and invariant scenarios in
isolated PostgreSQL 17 + pgvector environment:
1. Initial upload creates active document
2. Identical file re-upload returns duplicate (200, no DB insert)
3. Modified file with replace_active=False returns 409 Conflict (active doc preserved)
4. Modified file with replace_active=True without expected_current_sha256 fails (400)
5. Modified file with replace_active=True with valid expected_current_sha256 replaces atomically
6. Dedicated POST /documents/replace requires expected_current_sha256 (422 if missing)
7. Dedicated POST /documents/replace with mismatched expected_current_sha256 returns 409 Conflict
8. Dedicated POST /documents/replace with valid expected_current_sha256 replaces atomically
9. Client expected_sha256 mismatch fails closed (400, active doc preserved)
10. Embedding generation failure preserves active version (fails before DB tx)
11. Chunk INSERT failure triggers atomic rollback (active doc preserved, 0 partial docs)
12. Status transition DB error triggers atomic rollback (active doc preserved)
13. Concurrent updates with same expected_current_hash: exactly 1 succeeds, 1 gets 409 (Lost Update prevented)
14. Stale expected hash rejected (409) and retry with updated latest hash succeeds
15. Partial unique index constraint prevents multi-active documents at DB level (UniqueViolation)
16. Search visibility: new active version exposed, old archived version excluded
17. Previous document revision history and provenance preserved
18. Project isolation / cross-project write access denied (403 Forbidden)
19. Cross-project same path isolation (Project 1 vs Project 6)
20. Different paths same project isolation (path_a vs path_b)
21. Strict global invariant: no path has 0 active or >1 active documents, 0 orphan chunks
22. Existing upload API backward compatibility (upload without replace_active preserved 100%)
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

TEST_PG_SOCK = os.getenv("TEST_PG_SOCK", "/tmp/pg_sock_candidate_b3")
TEST_PG_PORT = os.getenv("TEST_PG_PORT", "5442")
TEST_PG_DB = os.getenv("TEST_PG_DB", "ai_hub_test_m3fe3")
TEST_PG_USER = os.getenv("TEST_PG_USER", "test_user_m3fe3")
TEST_PG_PASSWORD = os.getenv("TEST_PG_PASSWORD", "test_password_m3fe3")
TEST_STATIC_TOKEN = "m3fe3-atomic-renewal-test-secret-token"

os.environ["POSTGRES_HOST"] = TEST_PG_SOCK
os.environ["POSTGRES_PORT"] = TEST_PG_PORT
os.environ["POSTGRES_DB"] = TEST_PG_DB
os.environ["POSTGRES_USER"] = TEST_PG_USER
os.environ["POSTGRES_PASSWORD"] = TEST_PG_PASSWORD
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

TEST_UPLOAD_DIR = Path(tempfile.mkdtemp(prefix="ai_hub_m3fe3_uploads_"))
os.environ["DOCUMENTS_UPLOAD_DIR"] = str(TEST_UPLOAD_DIR)

import sys
app_dir = str(Path(__file__).resolve().parent.parent)
if app_dir not in sys.path:
    sys.path.insert(0, app_dir)

from fastapi.testclient import TestClient

from app.core.authorization import AuthorizationContext, Capability
from app.core.db import get_db_connection
from app.main import app
import app.main as main_module
import app.routers.documents as documents_router

documents_router.DOCUMENTS_UPLOAD_DIR = TEST_UPLOAD_DIR
main_module._REST_API_KEY = TEST_STATIC_TOKEN

TEST_COMMIT_SHA_V1 = "fd9742cf9cf2940654833fcf8c3c8d951adeb3e1"
TEST_COMMIT_SHA_V2 = "832103ceca1c0457266e8e7e284ca01f2a2738b3"
TEST_COMMIT_SHA_V3 = "9a7104beba1c0457266e8e7e284ca01f2a2744c8"
TEST_REL_PATH = "02_ros2_core_curriculum/docs/01_python_oop_and_ros2_node.md"
TEST_SOURCE_V1 = f"https://github.com/CHUNGJINWOO/learning-portfolio/blob/{TEST_COMMIT_SHA_V1}/{TEST_REL_PATH}"
TEST_SOURCE_V2 = f"https://github.com/CHUNGJINWOO/learning-portfolio/blob/{TEST_COMMIT_SHA_V2}/{TEST_REL_PATH}"
TEST_SOURCE_V3 = f"https://github.com/CHUNGJINWOO/learning-portfolio/blob/{TEST_COMMIT_SHA_V3}/{TEST_REL_PATH}"

CONTENT_V1 = b"# ROS 2 Python OOP Node\nVersion 1 initial curriculum text with rclpy basics.\n"
CONTENT_V2 = b"# ROS 2 Python OOP Node - Revised\nVersion 2 expanded curriculum with QoS profile parameters.\n"
CONTENT_V3 = b"# ROS 2 Python OOP Node - Final\nVersion 3 production-grade node lifecycle management.\n"


def is_candidate_db_available() -> bool:
    try:
        with get_db_connection() as conn:
            row = conn.execute("SELECT 1;").fetchone()
            return row is not None
    except Exception:
        return False


@unittest.skipUnless(
    is_candidate_db_available(),
    "Isolated PostgreSQL candidate test database is not running or accessible",
)
class AtomicVersionRenewalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved_env = {
            k: os.environ.get(k)
            for k in [
                "AIHUB_STATIC_KEY_READ_PROJECTS",
                "AIHUB_STATIC_KEY_WRITE_PROJECTS",
                "MCP_ACCESS_TOKEN",
            ]
        }
        os.environ["AIHUB_STATIC_KEY_READ_PROJECTS"] = "1,6"
        os.environ["AIHUB_STATIC_KEY_WRITE_PROJECTS"] = "6"
        os.environ["MCP_ACCESS_TOKEN"] = TEST_STATIC_TOKEN

        cls.client = TestClient(app)
        cls.auth_headers = {"Authorization": f"Bearer {TEST_STATIC_TOKEN}"}

        # Seed Projects and apply partial unique index migration in the isolated test database
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO projects (id, name, slug, description, status)
                VALUES (6, 'Learning Portfolio', 'learning-portfolio', 'ROS 2 robotics curriculum', 'active')
                ON CONFLICT (id) DO UPDATE SET status = 'active';
                """
            )
            conn.execute(
                """
                INSERT INTO projects (id, name, slug, description, status)
                VALUES (1, 'Guidebook', 'guidebook', 'Main operational guidebook', 'active')
                ON CONFLICT (id) DO UPDATE SET status = 'active';
                """
            )
            # Apply partial unique index if not already present
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_active_project_path
                ON public.documents (project_id, relative_path)
                WHERE status = 'active'
                  AND project_id IS NOT NULL
                  AND relative_path IS NOT NULL;

                CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_active_global_path
                ON public.documents (relative_path)
                WHERE status = 'active'
                  AND project_id IS NULL
                  AND relative_path IS NOT NULL;
                """
            )
            conn.execute("DELETE FROM document_chunks;")
            conn.execute("DELETE FROM documents;")
            conn.commit()

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(TEST_UPLOAD_DIR, ignore_errors=True)

    def setUp(self):
        # Reset documents before each test
        with get_db_connection() as conn:
            conn.execute("DELETE FROM document_chunks;")
            conn.execute("DELETE FROM documents;")
            conn.commit()

    def test_01_initial_upload_creates_active_document(self):
        """Scenario 1: Initial upload creates an active document and chunks."""
        sha_v1 = hashlib.sha256(CONTENT_V1).hexdigest()
        data = {
            "project_id": 6,
            "relative_path": TEST_REL_PATH,
            "source": TEST_SOURCE_V1,
            "commit_sha": TEST_COMMIT_SHA_V1,
            "expected_sha256": sha_v1,
        }
        files = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V1), "text/markdown")}
        resp = self.client.post("/documents/upload", data=data, files=files, headers=self.auth_headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()

        self.assertEqual(payload["status"], "created")
        self.assertEqual(payload["project_id"], 6)
        self.assertEqual(payload["relative_path"], TEST_REL_PATH)
        self.assertEqual(payload["sha256"], sha_v1)
        doc_id = payload["document_id"]

        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT id, status, relative_path, sha256 FROM documents WHERE id = %s;",
                (doc_id,)
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[1], "active")
            self.assertEqual(row[2], TEST_REL_PATH)
            self.assertEqual(row[3], sha_v1)

            chunks = conn.execute(
                "SELECT count(*) FROM document_chunks WHERE document_id = %s;",
                (doc_id,)
            ).fetchone()
            self.assertGreater(chunks[0], 0)

    def test_02_identical_reupload_returns_duplicate(self):
        """Scenario 2: Re-uploading identical content returns duplicate with 0 new DB inserts."""
        sha_v1 = hashlib.sha256(CONTENT_V1).hexdigest()
        data = {
            "project_id": 6,
            "relative_path": TEST_REL_PATH,
            "source": TEST_SOURCE_V1,
            "commit_sha": TEST_COMMIT_SHA_V1,
        }
        files = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V1), "text/markdown")}
        # First upload
        r1 = self.client.post("/documents/upload", data=data, files=files, headers=self.auth_headers)
        self.assertEqual(r1.status_code, 200)
        doc1_id = r1.json()["document_id"]

        # Duplicate re-upload (with replace_active=False)
        files = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V1), "text/markdown")}
        r2 = self.client.post("/documents/upload", data=data, files=files, headers=self.auth_headers)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["status"], "duplicate")
        self.assertEqual(r2.json()["document_id"], doc1_id)

        # Duplicate re-upload via replace endpoint (with expected_current_sha256=sha_v1)
        data_rep = {**data, "expected_current_sha256": sha_v1}
        files = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V1), "text/markdown")}
        r3 = self.client.post("/documents/replace", data=data_rep, files=files, headers=self.auth_headers)
        self.assertEqual(r3.status_code, 200)
        self.assertEqual(r3.json()["status"], "duplicate")
        self.assertEqual(r3.json()["document_id"], doc1_id)

        # Verify only 1 document in DB
        with get_db_connection() as conn:
            cnt = conn.execute("SELECT count(*) FROM documents;").fetchone()[0]
            self.assertEqual(cnt, 1)

    def test_03_modified_file_without_replace_active_returns_409(self):
        """Scenario 3: Same path, different SHA without replace_active returns 409 Conflict."""
        # Initial upload v1
        data1 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V1}
        f1 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V1), "text/markdown")}
        r1 = self.client.post("/documents/upload", data=data1, files=f1, headers=self.auth_headers)
        self.assertEqual(r1.status_code, 200)
        doc1_id = r1.json()["document_id"]

        # Upload v2 without replace_active
        data2 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V2}
        f2 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V2), "text/markdown")}
        r2 = self.client.post("/documents/upload", data=data2, files=f2, headers=self.auth_headers)
        self.assertEqual(r2.status_code, 409)
        self.assertIn("Automatic replacement is disabled", r2.json()["detail"])

        # Verify v1 is still active and no second doc exists
        with get_db_connection() as conn:
            active = conn.execute("SELECT id, status FROM documents WHERE relative_path = %s;", (TEST_REL_PATH,)).fetchall()
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0][0], doc1_id)
            self.assertEqual(active[0][1], "active")

    def test_04_replace_active_without_expected_current_sha256_fails(self):
        """Scenario 4: Attempting replace_active=True on /documents/upload without expected_current_sha256 returns 400."""
        # Initial upload v1
        data1 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V1}
        f1 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V1), "text/markdown")}
        r1 = self.client.post("/documents/upload", data=data1, files=f1, headers=self.auth_headers)
        self.assertEqual(r1.status_code, 200)

        # Upload v2 with replace_active=True but missing expected_current_sha256
        data2 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V2, "replace_active": "true"}
        f2 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V2), "text/markdown")}
        r2 = self.client.post("/documents/upload", data=data2, files=f2, headers=self.auth_headers)
        self.assertEqual(r2.status_code, 400)
        self.assertIn("requires 'expected_current_sha256'", r2.json()["detail"])

    def test_05_replace_active_with_valid_expected_current_sha256_replaces_atomically(self):
        """Scenario 5: Calling /documents/upload with replace_active=True and valid expected_current_sha256 succeeds."""
        sha_v1 = hashlib.sha256(CONTENT_V1).hexdigest()
        sha_v2 = hashlib.sha256(CONTENT_V2).hexdigest()

        # Step 1: Upload v1
        data1 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V1, "commit_sha": TEST_COMMIT_SHA_V1}
        f1 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V1), "text/markdown")}
        r1 = self.client.post("/documents/upload", data=data1, files=f1, headers=self.auth_headers)
        doc1_id = r1.json()["document_id"]

        # Step 2: Upload v2 with replace_active=True and expected_current_sha256
        data2 = {
            "project_id": 6,
            "relative_path": TEST_REL_PATH,
            "source": TEST_SOURCE_V2,
            "commit_sha": TEST_COMMIT_SHA_V2,
            "expected_sha256": sha_v2,
            "expected_current_sha256": sha_v1,
            "replace_active": "true",
        }
        f2 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V2), "text/markdown")}
        r2 = self.client.post("/documents/upload", data=data2, files=f2, headers=self.auth_headers)
        self.assertEqual(r2.status_code, 200, r2.text)
        payload = r2.json()

        self.assertEqual(payload["status"], "replaced")
        doc2_id = payload["document_id"]
        self.assertEqual(payload["replaced_document_id"], doc1_id)
        self.assertEqual(payload["sha256"], sha_v2)
        self.assertEqual(payload["previous_sha256"], sha_v1)

        with get_db_connection() as conn:
            cnt = conn.execute("SELECT count(*) FROM documents WHERE relative_path = %s AND status = 'active';", (TEST_REL_PATH,)).fetchone()[0]
            self.assertEqual(cnt, 1)

    def test_06_dedicated_replace_endpoint_requires_expected_current_sha256(self):
        """Scenario 6: POST /documents/replace requires expected_current_sha256; missing param returns 422."""
        data = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V2}
        files = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V2), "text/markdown")}
        resp = self.client.post("/documents/replace", data=data, files=files, headers=self.auth_headers)
        self.assertEqual(resp.status_code, 422)

    def test_07_dedicated_replace_endpoint_with_mismatched_expected_current_sha256_returns_409(self):
        """Scenario 7: POST /documents/replace with mismatched expected_current_sha256 returns 409 Conflict."""
        # Step 1: Upload v1
        data1 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V1}
        f1 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V1), "text/markdown")}
        doc1_id = self.client.post("/documents/upload", data=data1, files=f1, headers=self.auth_headers).json()["document_id"]

        # Step 2: Attempt replace with bogus current SHA
        bogus_sha = "a" * 64
        data2 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V2, "expected_current_sha256": bogus_sha}
        f2 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V2), "text/markdown")}
        r2 = self.client.post("/documents/replace", data=data2, files=f2, headers=self.auth_headers)
        self.assertEqual(r2.status_code, 409)
        self.assertIn("does not match expected_current_sha256", r2.json()["detail"])

        # Verify doc1 is still active and untouched
        with get_db_connection() as conn:
            docs = conn.execute("SELECT id, status FROM documents WHERE relative_path = %s;", (TEST_REL_PATH,)).fetchall()
            self.assertEqual(len(docs), 1)
            self.assertEqual(docs[0][0], doc1_id)
            self.assertEqual(docs[0][1], "active")

    def test_08_dedicated_replace_endpoint_success(self):
        """Scenario 8: POST /documents/replace with valid expected_current_sha256 replaces document atomically."""
        sha_v1 = hashlib.sha256(CONTENT_V1).hexdigest()
        sha_v2 = hashlib.sha256(CONTENT_V2).hexdigest()

        # Step 1: Upload v1
        data1 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V1}
        f1 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V1), "text/markdown")}
        doc1_id = self.client.post("/documents/upload", data=data1, files=f1, headers=self.auth_headers).json()["document_id"]

        # Step 2: Replace via /documents/replace
        data2 = {
            "project_id": 6,
            "relative_path": TEST_REL_PATH,
            "source": TEST_SOURCE_V2,
            "expected_current_sha256": sha_v1,
            "expected_sha256": sha_v2,
        }
        f2 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V2), "text/markdown")}
        r2 = self.client.post("/documents/replace", data=data2, files=f2, headers=self.auth_headers)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["status"], "replaced")
        self.assertEqual(r2.json()["replaced_document_id"], doc1_id)
        doc2_id = r2.json()["document_id"]

        # Step 3: Check DB state
        with get_db_connection() as conn:
            rows = conn.execute("SELECT id, status, sha256 FROM documents WHERE relative_path = %s ORDER BY id ASC;", (TEST_REL_PATH,)).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0][0], doc1_id)
            self.assertEqual(rows[0][1], "archived")
            self.assertEqual(rows[1][0], doc2_id)
            self.assertEqual(rows[1][1], "active")

    def test_09_expected_sha256_mismatch_fails_closed(self):
        """Scenario 9: Client expected_sha256 mismatch rejects request (400) and preserves active doc."""
        sha_v1 = hashlib.sha256(CONTENT_V1).hexdigest()
        data1 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V1}
        f1 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V1), "text/markdown")}
        doc1_id = self.client.post("/documents/upload", data=data1, files=f1, headers=self.auth_headers).json()["document_id"]

        data2 = {
            "project_id": 6,
            "relative_path": TEST_REL_PATH,
            "source": TEST_SOURCE_V2,
            "expected_current_sha256": sha_v1,
            "expected_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
        }
        f2 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V2), "text/markdown")}
        r2 = self.client.post("/documents/replace", data=data2, files=f2, headers=self.auth_headers)
        self.assertEqual(r2.status_code, 400)
        self.assertIn("does not match expected SHA-256", r2.json()["detail"])

        with get_db_connection() as conn:
            row = conn.execute("SELECT status FROM documents WHERE id = %s;", (doc1_id,)).fetchone()
            self.assertEqual(row[0], "active")

    def test_10_embedding_generation_failure_preserves_active_version(self):
        """Scenario 10: Failure during embedding generation occurs before DB transaction; active doc remains untouched."""
        sha_v1 = hashlib.sha256(CONTENT_V1).hexdigest()
        data1 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V1}
        f1 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V1), "text/markdown")}
        doc1_id = self.client.post("/documents/upload", data=data1, files=f1, headers=self.auth_headers).json()["document_id"]

        # Patch model.encode to raise an exception simulating OOM or model failure
        with patch("app.routers.documents.model.encode", side_effect=RuntimeError("Simulated neural model inference failure")):
            data2 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V2, "expected_current_sha256": sha_v1}
            f2 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V2), "text/markdown")}
            r2 = self.client.post("/documents/replace", data=data2, files=f2, headers=self.auth_headers)
            self.assertEqual(r2.status_code, 500)

        # Verify old doc is STILL active, no second doc created
        with get_db_connection() as conn:
            docs = conn.execute("SELECT id, status FROM documents WHERE relative_path = %s;", (TEST_REL_PATH,)).fetchall()
            self.assertEqual(len(docs), 1)
            self.assertEqual(docs[0][0], doc1_id)
            self.assertEqual(docs[0][1], "active")

    def test_11_chunk_insert_failure_triggers_atomic_rollback(self):
        """Scenario 11: Failure during chunk INSERT rolls back entire transaction; old doc remains active, 0 new docs."""
        sha_v1 = hashlib.sha256(CONTENT_V1).hexdigest()
        data1 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V1}
        f1 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V1), "text/markdown")}
        doc1_id = self.client.post("/documents/upload", data=data1, files=f1, headers=self.auth_headers).json()["document_id"]

        real_get_conn = get_db_connection

        class FailingConnWrapper:
            def __init__(self, real_conn):
                self._conn = real_conn

            def __enter__(self):
                self._c = self._conn.__enter__()
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                return self._conn.__exit__(exc_type, exc_val, exc_tb)

            def execute(self, query, params=None):
                if "INSERT INTO document_chunks" in query:
                    raise Exception("Simulated DB Disk Full or Foreign Key Violation during Chunk INSERT")
                return self._c.execute(query, params)

            def commit(self):
                self._c.commit()

        call_count = 0
        def patched_get_conn():
            nonlocal call_count
            call_count += 1
            raw = real_get_conn()
            if call_count >= 2:  # Phase 3 transaction
                return FailingConnWrapper(raw)
            return raw

        with patch("app.routers.documents.get_db_connection", side_effect=patched_get_conn):
            data2 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V2, "expected_current_sha256": sha_v1}
            f2 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V2), "text/markdown")}
            r2 = self.client.post("/documents/replace", data=data2, files=f2, headers=self.auth_headers)
            self.assertEqual(r2.status_code, 500)

        # Verify strict rollback: v1 is still active, total docs is still 1
        with get_db_connection() as conn:
            docs = conn.execute("SELECT id, status FROM documents;").fetchall()
            self.assertEqual(len(docs), 1)
            self.assertEqual(docs[0][0], doc1_id)
            self.assertEqual(docs[0][1], "active")

    def test_12_status_transition_db_error_triggers_atomic_rollback(self):
        """Scenario 12: Error during status update rolls back transaction; active document preserved."""
        sha_v1 = hashlib.sha256(CONTENT_V1).hexdigest()
        data1 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V1}
        f1 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V1), "text/markdown")}
        doc1_id = self.client.post("/documents/upload", data=data1, files=f1, headers=self.auth_headers).json()["document_id"]

        real_get_conn = get_db_connection

        class FailingStatusConnWrapper:
            def __init__(self, real_conn):
                self._conn = real_conn

            def __enter__(self):
                self._c = self._conn.__enter__()
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                return self._conn.__exit__(exc_type, exc_val, exc_tb)

            def execute(self, query, params=None):
                if "UPDATE documents" in query and "SET status = 'archived'" in query:
                    raise Exception("Simulated DB Deadlock / Lock Timeout during status transition")
                return self._c.execute(query, params)

            def commit(self):
                self._c.commit()

        call_count = 0
        def patched_get_conn():
            nonlocal call_count
            call_count += 1
            raw = real_get_conn()
            if call_count >= 2:
                return FailingStatusConnWrapper(raw)
            return raw

        with patch("app.routers.documents.get_db_connection", side_effect=patched_get_conn):
            data2 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V2, "expected_current_sha256": sha_v1}
            f2 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V2), "text/markdown")}
            r2 = self.client.post("/documents/replace", data=data2, files=f2, headers=self.auth_headers)
            self.assertEqual(r2.status_code, 500)

        with get_db_connection() as conn:
            row = conn.execute("SELECT status FROM documents WHERE id = %s;", (doc1_id,)).fetchone()
            self.assertEqual(row[0], "active")

    def test_13_concurrent_updates_same_expected_current_hash_lost_update_prevented(self):
        """Scenario 13: Two concurrent replacement requests with identical expected_current_sha256 serialize;
        exactly 1 succeeds with 200 replaced, the second receives 409 Conflict. Lost Update is completely prevented."""
        sha_v1 = hashlib.sha256(CONTENT_V1).hexdigest()
        data1 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V1}
        f1 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V1), "text/markdown")}
        r1 = self.client.post("/documents/upload", data=data1, files=f1, headers=self.auth_headers)
        self.assertEqual(r1.status_code, 200)

        content_a = b"# V2 Variant A\nExclusive concurrent edit A\n"
        content_b = b"# V2 Variant B\nExclusive concurrent edit B\n"

        def post_replace(content):
            c = TestClient(app)
            data = {
                "project_id": 6,
                "relative_path": TEST_REL_PATH,
                "source": TEST_SOURCE_V2,
                "expected_current_sha256": sha_v1,
            }
            files = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(content), "text/markdown")}
            return c.post("/documents/replace", data=data, files=files, headers=self.auth_headers)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut_a = executor.submit(post_replace, content_a)
            fut_b = executor.submit(post_replace, content_b)
            res_a = fut_a.result()
            res_b = fut_b.result()

        statuses = sorted([res_a.status_code, res_b.status_code])
        # Exactly one must succeed (200) and one must be rejected by CAS under lock (409)!
        self.assertEqual(statuses, [200, 409], f"Expected [200, 409], got {statuses}. res_a={res_a.text}, res_b={res_b.text}")

        # Invariant: Exactly ONE active document in DB!
        with get_db_connection() as conn:
            active_docs = conn.execute(
                "SELECT id, sha256 FROM documents WHERE relative_path = %s AND status = 'active';",
                (TEST_REL_PATH,)
            ).fetchall()
            self.assertEqual(len(active_docs), 1, f"Expected 1 active doc, got {len(active_docs)}")

    def test_14_sequential_stale_expected_hash_rejected_and_retry_with_new_hash_succeeds(self):
        """Scenario 14: Sequential renewal: stale expected hash rejected (409), followed by retry with updated latest hash succeeds."""
        sha_v1 = hashlib.sha256(CONTENT_V1).hexdigest()
        sha_v2 = hashlib.sha256(CONTENT_V2).hexdigest()
        sha_v3 = hashlib.sha256(CONTENT_V3).hexdigest()

        # Step 1: Upload v1
        d1 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V1}
        f1 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V1), "text/markdown")}
        self.client.post("/documents/upload", data=d1, files=f1, headers=self.auth_headers)

        # Step 2: Replace v1 -> v2
        d2 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V2, "expected_current_sha256": sha_v1}
        f2 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V2), "text/markdown")}
        r2 = self.client.post("/documents/replace", data=d2, files=f2, headers=self.auth_headers)
        self.assertEqual(r2.status_code, 200)
        doc2_id = r2.json()["document_id"]

        # Step 3: Attempt replace v2 -> v3 using stale sha_v1 -> 409 Conflict!
        d3_stale = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V3, "expected_current_sha256": sha_v1}
        f3 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V3), "text/markdown")}
        r3_stale = self.client.post("/documents/replace", data=d3_stale, files=f3, headers=self.auth_headers)
        self.assertEqual(r3_stale.status_code, 409)

        # Verify v2 is STILL the active document
        with get_db_connection() as conn:
            active = conn.execute("SELECT id, sha256 FROM documents WHERE relative_path = %s AND status = 'active';", (TEST_REL_PATH,)).fetchone()
            self.assertEqual(active[0], doc2_id)
            self.assertEqual(active[1], sha_v2)

        # Step 4: Retry with correct latest sha_v2 -> 200 OK replaced!
        d3_fresh = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V3, "expected_current_sha256": sha_v2}
        f3_fresh = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V3), "text/markdown")}
        r3_fresh = self.client.post("/documents/replace", data=d3_fresh, files=f3_fresh, headers=self.auth_headers)
        self.assertEqual(r3_fresh.status_code, 200)
        self.assertEqual(r3_fresh.json()["status"], "replaced")
        self.assertEqual(r3_fresh.json()["replaced_document_id"], doc2_id)

        # Verify v3 is now active
        with get_db_connection() as conn:
            active3 = conn.execute("SELECT id, sha256 FROM documents WHERE relative_path = %s AND status = 'active';", (TEST_REL_PATH,)).fetchone()
            self.assertEqual(active3[1], sha_v3)

    def test_15_unique_index_constraint_prevents_multi_active_documents(self):
        """Scenario 15: Database partial unique index uq_documents_active_project_path rejects any bypass attempt
        to insert multiple active documents for the same (project_id, relative_path)."""
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO documents (project_id, title, relative_path, status, sha256)
                VALUES (6, 'Doc A', %s, 'active', 'sha111');
                """,
                (TEST_REL_PATH,)
            )
            conn.commit()

            # Attempting direct SQL insert of second active doc with same path MUST raise UniqueViolation
            with self.assertRaises(Exception) as ctx:
                conn.execute(
                    """
                    INSERT INTO documents (project_id, title, relative_path, status, sha256)
                    VALUES (6, 'Doc B', %s, 'active', 'sha222');
                    """,
                    (TEST_REL_PATH,)
                )
            self.assertIn("uq_documents_active_project_path", str(ctx.exception))
            conn.rollback()

    def test_16_search_visibility_new_active_exposed_old_archived_excluded(self):
        """Scenario 16: Semantic search returns new active version chunks and excludes archived version chunks."""
        sha_v1 = hashlib.sha256(CONTENT_V1).hexdigest()
        unique_old = b"# Document Title\nCurriculumBasicsOldToken explains basic topics.\n"
        data1 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V1}
        f1 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(unique_old), "text/markdown")}
        r1 = self.client.post("/documents/upload", data=data1, files=f1, headers=self.auth_headers)
        doc1_id = r1.json()["document_id"]
        old_sha = r1.json()["sha256"]

        unique_new = b"# Document Title\nCurriculumRevisedNewToken explains advanced QoS topics.\n"
        data2 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V2, "expected_current_sha256": old_sha}
        f2 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(unique_new), "text/markdown")}
        r2 = self.client.post("/documents/replace", data=data2, files=f2, headers=self.auth_headers)
        doc2_id = r2.json()["document_id"]

        search_res_new = self.client.get(
            "/documents/search",
            params={"q": "CurriculumRevisedNewToken", "project_id": 6},
            headers=self.auth_headers,
        )
        self.assertEqual(search_res_new.status_code, 200)
        items_new = search_res_new.json().get("results", [])
        self.assertTrue(any(item.get("document_id") == doc2_id for item in items_new))
        self.assertFalse(any(item.get("document_id") == doc1_id for item in items_new))

        search_res_old = self.client.get(
            "/documents/search",
            params={"q": "CurriculumBasicsOldToken", "project_id": 6},
            headers=self.auth_headers,
        )
        self.assertEqual(search_res_old.status_code, 200)
        items_old = search_res_old.json().get("results", [])
        self.assertFalse(any(item.get("document_id") == doc1_id for item in items_old), "Archived doc chunks leaked in search!")

    def test_17_previous_document_history_and_provenance_preserved(self):
        """Scenario 17: Previous document rows remain in DB with status='archived', original SHA, and link in new doc."""
        sha1 = hashlib.sha256(CONTENT_V1).hexdigest()
        sha2 = hashlib.sha256(CONTENT_V2).hexdigest()

        d1 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V1, "commit_sha": TEST_COMMIT_SHA_V1}
        f1 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V1), "text/markdown")}
        doc1_id = self.client.post("/documents/upload", data=d1, files=f1, headers=self.auth_headers).json()["document_id"]

        d2 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V2, "commit_sha": TEST_COMMIT_SHA_V2, "expected_current_sha256": sha1}
        f2 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V2), "text/markdown")}
        doc2_id = self.client.post("/documents/replace", data=d2, files=f2, headers=self.auth_headers).json()["document_id"]

        with get_db_connection() as conn:
            history = conn.execute(
                """
                SELECT id, status, sha256, description, created_at
                FROM documents
                WHERE relative_path = %s
                ORDER BY id ASC;
                """,
                (TEST_REL_PATH,)
            ).fetchall()

            self.assertEqual(len(history), 2)
            self.assertEqual(history[0][0], doc1_id)
            self.assertEqual(history[0][1], "archived")
            self.assertEqual(history[0][2], sha1)

            self.assertEqual(history[1][0], doc2_id)
            self.assertEqual(history[1][1], "active")
            self.assertEqual(history[1][2], sha2)

            meta2 = json.loads(history[1][3])
            self.assertEqual(meta2["supersedes_doc_id"], doc1_id)
            self.assertEqual(meta2["previous_sha256"], sha1)

    def test_18_project_isolation_access_denied(self):
        """Scenario 18: Replacing or uploading to unauthorized project returns strict 403 Forbidden."""
        scoped_ctx = AuthorizationContext(
            identity="sa-portfolio-syncer",
            read_project_ids=frozenset([6]),
            write_project_ids=frozenset([6]),
            capabilities=frozenset(),
        )

        data = {"project_id": 1, "relative_path": "docs/test.md", "expected_current_sha256": "0" * 64}
        files = {"file": ("test.md", io.BytesIO(b"# Guidebook doc"), "text/markdown")}

        client = TestClient(app)
        with patch("app.main.verify_rest_token", return_value=scoped_ctx):
            resp = client.post("/documents/replace", data=data, files=files, headers={"Authorization": "Bearer fake-token"})
            self.assertEqual(resp.status_code, 403)
            self.assertIn("not authorized", resp.json()["detail"].lower())

    def test_19_cross_project_same_path_isolation(self):
        """Scenario 19: Documents with identical relative_path across different projects remain strictly isolated."""
        shared_rel = "docs/architecture.md"
        src_p1 = f"https://github.com/CHUNGJINWOO/guidebook/blob/{TEST_COMMIT_SHA_V1}/{shared_rel}"
        src_p6 = f"https://github.com/CHUNGJINWOO/learning-portfolio/blob/{TEST_COMMIT_SHA_V1}/{shared_rel}"

        dual_ctx = AuthorizationContext(
            identity="service:multi-project-admin",
            read_project_ids=frozenset([1, 6]),
            write_project_ids=frozenset([1, 6]),
            capabilities=frozenset(),
        )

        with patch("app.main.verify_rest_token", return_value=dual_ctx):
            # 1. Upload to Project 1
            f1 = {"file": ("architecture.md", io.BytesIO(b"# Guidebook Architecture\n"), "text/markdown")}
            d1 = {"project_id": 1, "relative_path": shared_rel, "source": src_p1}
            r1 = self.client.post("/documents/upload", data=d1, files=f1, headers=self.auth_headers)
            self.assertEqual(r1.status_code, 200)
            doc_p1_id = r1.json()["document_id"]

            # 2. Upload to Project 6
            f6 = {"file": ("architecture.md", io.BytesIO(b"# Portfolio Architecture V1\n"), "text/markdown")}
            d6 = {"project_id": 6, "relative_path": shared_rel, "source": src_p6}
            r6 = self.client.post("/documents/upload", data=d6, files=f6, headers=self.auth_headers)
            self.assertEqual(r6.status_code, 200)
            p6_v1_sha = r6.json()["sha256"]

            # 3. Replace in Project 6
            f6_v2 = {"file": ("architecture.md", io.BytesIO(b"# Portfolio Architecture V2\n"), "text/markdown")}
            d6_v2 = {"project_id": 6, "relative_path": shared_rel, "source": src_p6, "expected_current_sha256": p6_v1_sha}
            r6_v2 = self.client.post("/documents/replace", data=d6_v2, files=f6_v2, headers=self.auth_headers)
            self.assertEqual(r6_v2.status_code, 200)
            self.assertEqual(r6_v2.json()["status"], "replaced")

            # 4. Verify Project 1 doc is STILL active and untouched!
            with get_db_connection() as conn:
                p1_doc = conn.execute("SELECT id, status FROM documents WHERE id = %s;", (doc_p1_id,)).fetchone()
                self.assertEqual(p1_doc[1], "active")

                p6_docs = conn.execute(
                    "SELECT id, status FROM documents WHERE project_id = 6 AND relative_path = %s ORDER BY id ASC;",
                    (shared_rel,)
                ).fetchall()
                self.assertEqual(len(p6_docs), 2)
                self.assertEqual(p6_docs[0][1], "archived")
                self.assertEqual(p6_docs[1][1], "active")

    def test_20_different_paths_same_project_isolation(self):
        """Scenario 20: Replacing path_a does not affect path_b in the same project."""
        path_a = "02_ros2_core_curriculum/docs/01_python_oop_and_ros2_node.md"
        path_b = "02_ros2_core_curriculum/docs/02_parameters_and_qos.md"
        src_a = f"https://github.com/CHUNGJINWOO/learning-portfolio/blob/{TEST_COMMIT_SHA_V1}/{path_a}"
        src_b = f"https://github.com/CHUNGJINWOO/learning-portfolio/blob/{TEST_COMMIT_SHA_V1}/{path_b}"

        fa = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(b"# Doc A\n"), "text/markdown")}
        da = {"project_id": 6, "relative_path": path_a, "source": src_a}
        r_a = self.client.post("/documents/upload", data=da, files=fa, headers=self.auth_headers)
        doc_a_id = r_a.json()["document_id"]
        sha_a = r_a.json()["sha256"]

        fb = {"file": ("02_parameters_and_qos.md", io.BytesIO(b"# Doc B\n"), "text/markdown")}
        db = {"project_id": 6, "relative_path": path_b, "source": src_b}
        doc_b_id = self.client.post("/documents/upload", data=db, files=fb, headers=self.auth_headers).json()["document_id"]

        fa_v2 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(b"# Doc A Rev\n"), "text/markdown")}
        da_v2 = {"project_id": 6, "relative_path": path_a, "source": src_a, "expected_current_sha256": sha_a}
        res_rep = self.client.post("/documents/replace", data=da_v2, files=fa_v2, headers=self.auth_headers)
        self.assertEqual(res_rep.status_code, 200)

        with get_db_connection() as conn:
            row_b = conn.execute("SELECT status FROM documents WHERE id = %s;", (doc_b_id,)).fetchone()
            self.assertEqual(row_b[0], "active")

    def test_21_strict_invariant_audit(self):
        """Scenario 21: Strict audit verifying no path has multiple active docs or orphan chunks."""
        sha_v1 = hashlib.sha256(CONTENT_V1).hexdigest()
        sha_v2 = hashlib.sha256(CONTENT_V2).hexdigest()

        d1 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V1}
        f1 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V1), "text/markdown")}
        self.client.post("/documents/upload", data=d1, files=f1, headers=self.auth_headers)

        d2 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V2, "expected_current_sha256": sha_v1}
        f2 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V2), "text/markdown")}
        self.client.post("/documents/replace", data=d2, files=f2, headers=self.auth_headers)

        content_v3 = b"# V3 Content\nThird revision of the document.\n"
        d3 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V2, "expected_current_sha256": sha_v2}
        f3 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(content_v3), "text/markdown")}
        self.client.post("/documents/replace", data=d3, files=f3, headers=self.auth_headers)

        with get_db_connection() as conn:
            active_count = conn.execute(
                "SELECT count(*) FROM documents WHERE relative_path = %s AND status = 'active';",
                (TEST_REL_PATH,)
            ).fetchone()[0]
            self.assertEqual(active_count, 1)

            archived_count = conn.execute(
                "SELECT count(*) FROM documents WHERE relative_path = %s AND status = 'archived';",
                (TEST_REL_PATH,)
            ).fetchone()[0]
            self.assertEqual(archived_count, 2)

            orphans = conn.execute(
                """
                SELECT count(*)
                FROM document_chunks dc
                LEFT JOIN documents d ON dc.document_id = d.id
                WHERE d.id IS NULL;
                """
            ).fetchone()[0]
            self.assertEqual(orphans, 0)

    def test_22_upload_api_backward_compatibility(self):
        """Scenario 22: Traditional /documents/upload without replacement maintains 100% backward compatibility."""
        # 1. Initial upload creates document
        f1 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V1), "text/markdown")}
        d1 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V1}
        r1 = self.client.post("/documents/upload", data=d1, files=f1, headers=self.auth_headers)
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.json()["status"], "created")

        # 2. Duplicate upload returns duplicate (200)
        f1_dup = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V1), "text/markdown")}
        r1_dup = self.client.post("/documents/upload", data=d1, files=f1_dup, headers=self.auth_headers)
        self.assertEqual(r1_dup.status_code, 200)
        self.assertEqual(r1_dup.json()["status"], "duplicate")

        # 3. Upload of modified file without replace_active returns 409 Conflict
        f2 = {"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(CONTENT_V2), "text/markdown")}
        d2 = {"project_id": 6, "relative_path": TEST_REL_PATH, "source": TEST_SOURCE_V2}
        r2 = self.client.post("/documents/upload", data=d2, files=f2, headers=self.auth_headers)
        self.assertEqual(r2.status_code, 409)
        self.assertIn("Automatic replacement is disabled", r2.json()["detail"])


def run_tests():
    suite = unittest.TestLoader().loadTestsFromTestCase(AtomicVersionRenewalTests)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
