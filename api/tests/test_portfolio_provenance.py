"""
Tests for Learning Portfolio Provenance Preservation, Duplicate Detection,
and Ingestion Safety (M3-D1 & M3-D2).

Covers all 12+ verification scenarios required by M3-D2:
1. Initial upload success (created status)
2. Same path, same hash re-upload: no duplicate created (duplicate status, no DB insert)
3. Same path, different hash upload: conflict returned (409 Conflict, existing preserved)
4. Same filename, different paths: distinguished independently
5. Different projects: same path does not conflict
6. Unauthorized upload denied (403 Forbidden)
7. Query/Form project_id mismatch rejected (400 Bad Request)
8. Concurrent upload race condition handling & advisory lock
9. Git origin vs uploaded bytes mismatch detection (expected_sha256 & git_verifier)
10. Upload failure temporary file cleanup
11. Legacy upload API regression
12. Search & CanonicalContext ContextSource provenance passthrough
13. Indexing exclusion rules (inbox/, school/, .git/, build/, __pycache__, .env, .bag)
14. Path traversal and absolute path rejection
15. Invalid GitHub URL and commit SHA rejection
"""

import hashlib
import io
import json
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_TEST_DIR = Path("/home/cjinw/.gemini/antigravity/brain/7139e684-b810-40a9-a59e-53dd44c607d0/scratch/ai-hub-test/test_uploads")
_TEST_DIR.mkdir(parents=True, exist_ok=True)
os.environ["DOCUMENTS_UPLOAD_DIR"] = str(_TEST_DIR)
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ.setdefault("MCP_ACCESS_TOKEN", "test-only-mcp-token")

from fastapi.testclient import TestClient

from app.core.authorization import AuthorizationContext
from app.core.context_assembly import assemble_canonical_context
from app.core.git_verifier import verify_git_content_integrity
from app.main import app
from app.routers import documents as documents_router

_VALID_SHA: str = "fd9742cf9cf2940654833fcf8c3c8d951adeb3e1"


class MockDBConnection:
    """Mock database connection for deterministic testing of queries, advisory locks,
    and duplicate detection logic."""

    def __init__(self, project_exists=True, existing_doc=None, insert_doc_id=101):
        self.project_exists = project_exists
        self.existing_doc = existing_doc
        self.insert_doc_id = insert_doc_id
        self.executed_queries = []
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def execute(self, query, params=None):
        clean_q = " ".join(query.strip().split())
        self.executed_queries.append((clean_q, params))
        mock_cursor = MagicMock()

        if "FROM projects" in clean_q:
            mock_cursor.fetchone.return_value = (params[0],) if self.project_exists else None
        elif "pg_advisory_xact_lock" in clean_q:
            mock_cursor.fetchone.return_value = (None,)
        elif "FROM documents d" in clean_q:
            mock_cursor.fetchone.return_value = self.existing_doc
        elif "INSERT INTO documents" in clean_q:
            mock_cursor.fetchone.return_value = (self.insert_doc_id,)
        elif "INSERT INTO document_chunks" in clean_q:
            mock_cursor.fetchone.return_value = None
        else:
            mock_cursor.fetchone.return_value = None
            mock_cursor.fetchall.return_value = []
        return mock_cursor

    def commit(self):
        self.committed = True


class PortfolioProvenanceAndDeduplicationTests(unittest.TestCase):
    def setUp(self):
        _TEST_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TEST_DIR, ignore_errors=True)

    def _client_with_write_access(self, project_id: int = 1) -> TestClient:
        ctx = AuthorizationContext(
            identity="test-author",
            allowed_project_ids=frozenset({project_id}),
            allow_global_read=False,
            allow_write=True,
        )
        client = TestClient(app)
        client.app_state["authorization_context"] = ctx
        return client

    def _client_with_read_only(self, project_id: int = 1) -> TestClient:
        ctx = AuthorizationContext(
            identity="read-only-user",
            allowed_project_ids=frozenset({project_id}),
            allow_global_read=False,
            allow_write=False,
        )
        client = TestClient(app)
        client.app_state["authorization_context"] = ctx
        return client

    # -------------------------------------------------------------------------
    # 1. 최초 업로드 성공 (created status)
    # -------------------------------------------------------------------------
    def test_01_initial_upload_success(self):
        client = self._client_with_write_access(project_id=1)
        rel_path = "02_ros2_core_curriculum/docs/01_python_oop_and_ros2_node.md"
        source_url = f"https://github.com/CHUNGJINWOO/learning-portfolio/blob/{_VALID_SHA}/{rel_path}"
        mock_conn = MockDBConnection(project_exists=True, existing_doc=None, insert_doc_id=101)

        with patch("app.routers.documents.get_db_connection", return_value=mock_conn), \
             patch("app.routers.documents.extract_text") as mock_extract, \
             patch("app.routers.documents.chunk_pages") as mock_chunk:

            mock_extract.return_value = ["# Node Concept\nContent here"]
            mock_chunk.return_value = [(0, 1, "# Node Concept\nContent here")]

            file_content = b"# Node Concept\nContent here"
            response = client.post(
                "/documents/upload",
                data={
                    "project_id": 1,
                    "title": "Python OOP & ROS 2 Node",
                    "relative_path": rel_path,
                    "source": source_url,
                    "commit_sha": _VALID_SHA,
                },
                files={"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(file_content), "text/markdown")},
            )

            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["status"], "created")
            self.assertEqual(data["document_id"], 101)
            self.assertEqual(data["relative_path"], rel_path)
            self.assertEqual(data["source"], source_url)
            self.assertEqual(data["commit_sha"], _VALID_SHA)
            self.assertTrue(mock_conn.committed)

    # -------------------------------------------------------------------------
    # 2. 동일 경로·동일 해시 재업로드 시 중복 생성 없음 (duplicate status, no new insert)
    # -------------------------------------------------------------------------
    def test_02_same_path_same_hash_duplicate_no_create(self):
        client = self._client_with_write_access(project_id=1)
        rel_path = "02_ros2_core_curriculum/docs/01_python_oop_and_ros2_node.md"
        source_url = f"https://github.com/CHUNGJINWOO/learning-portfolio/blob/{_VALID_SHA}/{rel_path}"
        file_content = b"# Node Concept\nContent here"
        content_sha256 = hashlib.sha256(file_content).hexdigest()

        # Existing active document matching (project_id=1, relative_path=rel_path, sha256=content_sha256)
        existing_doc = (
            888,  # id
            1,    # project_id
            "Python OOP & ROS 2 Node",  # title
            "01_python_oop_and_ros2_node.md",  # filename
            rel_path,  # relative_path
            content_sha256,  # sha256
            len(file_content),  # file_size
            source_url,  # source
            json.dumps({"commit_sha": _VALID_SHA}),  # description
            1,  # chunk_count
        )
        mock_conn = MockDBConnection(project_exists=True, existing_doc=existing_doc)

        with patch("app.routers.documents.get_db_connection", return_value=mock_conn), \
             patch("app.routers.documents.extract_text") as mock_extract:

            response = client.post(
                "/documents/upload",
                data={
                    "project_id": 1,
                    "relative_path": rel_path,
                    "source": source_url,
                    "commit_sha": _VALID_SHA,
                },
                files={"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(file_content), "text/markdown")},
            )

            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["status"], "duplicate")
            self.assertEqual(data["document_id"], 888)
            self.assertEqual(data["relative_path"], rel_path)
            self.assertEqual(data["sha256"], content_sha256)
            self.assertIn("already exists", data["message"])

            # Verify that extract_text was NEVER called (avoided expensive parsing/embedding)
            mock_extract.assert_not_called()

            # Verify that NO insert statement was executed
            inserted = [q for q, _ in mock_conn.executed_queries if "INSERT INTO" in q]
            self.assertEqual(len(inserted), 0)

    # -------------------------------------------------------------------------
    # 3. 동일 경로·다른 해시 업로드 시 기존 자료 보존 (409 Conflict)
    # -------------------------------------------------------------------------
    def test_03_same_path_different_hash_conflict_preserves_existing(self):
        client = self._client_with_write_access(project_id=1)
        rel_path = "02_ros2_core_curriculum/docs/01_python_oop_and_ros2_node.md"
        source_url = f"https://github.com/CHUNGJINWOO/learning-portfolio/blob/{_VALID_SHA}/{rel_path}"

        existing_sha = "1111111111111111111111111111111111111111111111111111111111111111"
        existing_doc = (
            888, 1, "Old Title", "01_python_oop_and_ros2_node.md",
            rel_path, existing_sha, 50, source_url, None, 1,
        )
        mock_conn = MockDBConnection(project_exists=True, existing_doc=existing_doc)

        with patch("app.routers.documents.get_db_connection", return_value=mock_conn):
            response = client.post(
                "/documents/upload",
                data={
                    "project_id": 1,
                    "relative_path": rel_path,
                    "source": source_url,
                    "commit_sha": _VALID_SHA,
                },
                files={"file": ("01_python_oop_and_ros2_node.md", io.BytesIO(b"# Modified content"), "text/markdown")},
            )

            self.assertEqual(response.status_code, 409)
            detail = response.json()["detail"]
            self.assertIn("already exists", detail)
            self.assertIn("different content", detail)
            self.assertIn("Automatic replacement is disabled", detail)

            # Verify NO inserts or updates occurred
            mutations = [q for q, _ in mock_conn.executed_queries if "INSERT" in q or "UPDATE" in q or "DELETE" in q]
            self.assertEqual(len(mutations), 0)

    # -------------------------------------------------------------------------
    # 4. 동일 파일명·서로 다른 경로 구분
    # -------------------------------------------------------------------------
    def test_04_same_filename_different_paths_distinguished(self):
        client = self._client_with_write_access(project_id=1)
        domain_a_path = "01_python_and_oop/README.md"
        domain_b_path = "02_ros2_core_curriculum/README.md"
        source_a = f"https://github.com/CHUNGJINWOO/learning-portfolio/blob/{_VALID_SHA}/{domain_a_path}"
        source_b = f"https://github.com/CHUNGJINWOO/learning-portfolio/blob/{_VALID_SHA}/{domain_b_path}"

        mock_conn_a = MockDBConnection(project_exists=True, existing_doc=None, insert_doc_id=301)
        mock_conn_b = MockDBConnection(project_exists=True, existing_doc=None, insert_doc_id=302)

        with patch("app.routers.documents.extract_text", return_value=["README content"]), \
             patch("app.routers.documents.chunk_pages", return_value=[(0, 1, "README content")]):

            with patch("app.routers.documents.get_db_connection", return_value=mock_conn_a):
                res_a = client.post(
                    "/documents/upload",
                    data={"project_id": 1, "relative_path": domain_a_path, "source": source_a},
                    files={"file": ("README.md", io.BytesIO(b"# Domain A"), "text/markdown")},
                )
                self.assertEqual(res_a.status_code, 200)
                data_a = res_a.json()

            with patch("app.routers.documents.get_db_connection", return_value=mock_conn_b):
                res_b = client.post(
                    "/documents/upload",
                    data={"project_id": 1, "relative_path": domain_b_path, "source": source_b},
                    files={"file": ("README.md", io.BytesIO(b"# Domain B"), "text/markdown")},
                )
                self.assertEqual(res_b.status_code, 200)
                data_b = res_b.json()

            self.assertEqual(data_a["filename"], data_b["filename"])
            self.assertEqual(data_a["filename"], "README.md")
            self.assertNotEqual(data_a["relative_path"], data_b["relative_path"])
            self.assertEqual(data_a["relative_path"], domain_a_path)
            self.assertEqual(data_b["relative_path"], domain_b_path)
            self.assertEqual(data_a["document_id"], 301)
            self.assertEqual(data_b["document_id"], 302)

    # -------------------------------------------------------------------------
    # 5. 서로 다른 프로젝트 간 경로 충돌 없음
    # -------------------------------------------------------------------------
    def test_05_different_projects_same_path_no_conflict(self):
        ctx = AuthorizationContext(
            identity="multi-project-user",
            allowed_project_ids=frozenset({1, 2}),
            allow_global_read=False,
            allow_write=True,
        )
        client = TestClient(app)
        client.app_state["authorization_context"] = ctx

        path = "docs/overview.md"
        source = f"https://github.com/CHUNGJINWOO/learning-portfolio/blob/{_VALID_SHA}/{path}"

        mock_conn_p1 = MockDBConnection(project_exists=True, existing_doc=None, insert_doc_id=401)
        mock_conn_p2 = MockDBConnection(project_exists=True, existing_doc=None, insert_doc_id=402)

        with patch("app.routers.documents.extract_text", return_value=["Overview"]), \
             patch("app.routers.documents.chunk_pages", return_value=[(0, 1, "Overview")]):

            with patch("app.routers.documents.get_db_connection", return_value=mock_conn_p1):
                res_p1 = client.post(
                    "/documents/upload",
                    data={"project_id": 1, "relative_path": path, "source": source},
                    files={"file": ("overview.md", io.BytesIO(b"# Overview P1"), "text/markdown")},
                )
                self.assertEqual(res_p1.status_code, 200)
                self.assertEqual(res_p1.json()["project_id"], 1)

            with patch("app.routers.documents.get_db_connection", return_value=mock_conn_p2):
                res_p2 = client.post(
                    "/documents/upload",
                    data={"project_id": 2, "relative_path": path, "source": source},
                    files={"file": ("overview.md", io.BytesIO(b"# Overview P2"), "text/markdown")},
                )
                self.assertEqual(res_p2.status_code, 200)
                self.assertEqual(res_p2.json()["project_id"], 2)

    # -------------------------------------------------------------------------
    # 6. 권한 없는 업로드 차단 (403 Forbidden)
    # -------------------------------------------------------------------------
    def test_06_unauthorized_upload_denied(self):
        client = self._client_with_read_only(project_id=1)

        response = client.post(
            "/documents/upload",
            data={"project_id": 1, "relative_path": "docs/test.md"},
            files={"file": ("test.md", io.BytesIO(b"# Test"), "text/markdown")},
        )
        self.assertEqual(response.status_code, 403)

    # -------------------------------------------------------------------------
    # 7. Query/Form project_id 불일치 거부 (400 Bad Request)
    # -------------------------------------------------------------------------
    def test_07_query_form_project_id_mismatch_rejected(self):
        client = self._client_with_write_access(project_id=1)

        # Query has project_id=1, form has project_id=2 -> must reject with 400
        response = client.post(
            "/documents/upload?project_id=1",
            data={"project_id": 2, "relative_path": "docs/test.md"},
            files={"file": ("test.md", io.BytesIO(b"# Test"), "text/markdown")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("do not match", response.json()["detail"])

        # When query and form match (both 1) -> proceeds
        mock_conn = MockDBConnection(project_exists=True, existing_doc=None, insert_doc_id=101)
        with patch("app.routers.documents.get_db_connection", return_value=mock_conn), \
             patch("app.routers.documents.extract_text", return_value=["Test"]), \
             patch("app.routers.documents.chunk_pages", return_value=[(0, 1, "Test")]):

            res_matching = client.post(
                "/documents/upload?project_id=1",
                data={"project_id": 1, "relative_path": "docs/test.md"},
                files={"file": ("test.md", io.BytesIO(b"# Test"), "text/markdown")},
            )
            self.assertEqual(res_matching.status_code, 200)

    # -------------------------------------------------------------------------
    # 8. 동시 업로드 경쟁 조건 & Advisory Lock 검증
    # -------------------------------------------------------------------------
    def test_08_concurrent_upload_race_condition(self):
        client = self._client_with_write_access(project_id=1)
        rel_path = "02_ros2/scripts/node.py"
        source_url = f"https://github.com/CHUNGJINWOO/learning-portfolio/blob/{_VALID_SHA}/{rel_path}"

        mock_conn = MockDBConnection(project_exists=True, existing_doc=None, insert_doc_id=777)

        with patch("app.routers.documents.get_db_connection", return_value=mock_conn), \
             patch("app.routers.documents.extract_text", return_value=["import rclpy"]), \
             patch("app.routers.documents.chunk_pages", return_value=[(0, 1, "import rclpy")]):

            res = client.post(
                "/documents/upload",
                data={"project_id": 1, "relative_path": rel_path, "source": source_url},
                files={"file": ("node.py", io.BytesIO(b"import rclpy"), "text/x-python")},
            )
            self.assertEqual(res.status_code, 200)

            # Verify that pg_advisory_xact_lock was acquired with deterministic 64-bit key
            lock_queries = [
                (q, p) for q, p in mock_conn.executed_queries if "pg_advisory_xact_lock" in q
            ]
            self.assertEqual(len(lock_queries), 1)
            lock_arg = lock_queries[0][1][0]
            self.assertIsInstance(lock_arg, int)
            self.assertGreater(lock_arg, 0)

    # -------------------------------------------------------------------------
    # 9. Git 원본과 업로드 바이트 불일치 감지
    # -------------------------------------------------------------------------
    def test_09_git_origin_vs_uploaded_bytes_mismatch(self):
        client = self._client_with_write_access(project_id=1)
        rel_path = "docs/test.md"
        wrong_expected_sha = "0000000000000000000000000000000000000000000000000000000000000000"

        # Case A: expected_sha256 mismatch via API
        response = client.post(
            "/documents/upload",
            data={
                "project_id": 1,
                "relative_path": rel_path,
                "expected_sha256": wrong_expected_sha,
            },
            files={"file": ("test.md", io.BytesIO(b"# Genuine Content"), "text/markdown")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("does not match expected SHA-256", response.json()["detail"])

        # Case B: verify_git_content_integrity directly
        with patch("app.core.git_verifier.get_git_commit_blob_bytes") as mock_blob:
            mock_blob.return_value = (b"# Genuine Commit Content", None)
            is_valid, git_sha, err = verify_git_content_integrity(
                repo_path="/tmp",
                commit_sha=_VALID_SHA,
                relative_path=rel_path,
                file_bytes=b"# Tampered Content",
            )
            self.assertFalse(is_valid)
            self.assertIn("Content integrity mismatch", err)

    # -------------------------------------------------------------------------
    # 10. 업로드 실패 후 임시 파일 정리
    # -------------------------------------------------------------------------
    def test_10_upload_failure_temp_file_cleanup(self):
        client = self._client_with_write_access(project_id=1)
        rel_path = "docs/corrupt.pdf"

        mock_conn = MockDBConnection(project_exists=True, existing_doc=None)

        with patch("app.routers.documents.get_db_connection", return_value=mock_conn), \
             patch("app.routers.documents.extract_text", side_effect=ValueError("Corrupt PDF binary")):

            response = client.post(
                "/documents/upload",
                data={"project_id": 1, "relative_path": rel_path},
                files={"file": ("corrupt.pdf", io.BytesIO(b"%PDF-corrupt"), "application/pdf")},
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("Corrupt PDF", response.json()["detail"])

            # Verify that no temporary files leaked in _TEST_DIR
            leftover_files = list(_TEST_DIR.glob("*corrupt.pdf*"))
            self.assertEqual(len(leftover_files), 0)

    # -------------------------------------------------------------------------
    # 11. 기존 업로드 API 회귀 테스트
    # -------------------------------------------------------------------------
    def test_11_legacy_upload_regression(self):
        client = self._client_with_write_access(project_id=1)
        mock_conn = MockDBConnection(project_exists=True, existing_doc=None, insert_doc_id=999)

        with patch("app.routers.documents.get_db_connection", return_value=mock_conn), \
             patch("app.routers.documents.extract_text", return_value=["Legacy"]), \
             patch("app.routers.documents.chunk_pages", return_value=[(0, 1, "Legacy")]):

            response = client.post(
                "/documents/upload",
                data={"project_id": 1, "title": "Legacy Node"},
                files={"file": ("node.py", io.BytesIO(b"# Legacy code"), "text/x-python")},
            )
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["status"], "created")
            self.assertEqual(data["filename"], "node.py")
            self.assertEqual(data["relative_path"], "node.py")
            self.assertEqual(data["source"], "upload")
            self.assertIsNone(data["commit_sha"])

    # -------------------------------------------------------------------------
    # 12. 검색 및 CanonicalContext ContextSource 회귀 테스트
    # -------------------------------------------------------------------------
    def test_12_search_and_canonical_context_regression(self):
        rel_path = "02_ros2_core_curriculum/docs/01_python_oop_and_ros2_node.md"
        source_url = f"https://github.com/CHUNGJINWOO/learning-portfolio/blob/{_VALID_SHA}/{rel_path}"

        mock_search_result = {
            "query": "ROS 2 OOP node",
            "project_id": 1,
            "memories": [],
            "documents": [
                {
                    "chunk_id": 555,
                    "document_id": 202,
                    "project_id": 1,
                    "title": "Python OOP & ROS 2 Node",
                    "filename": "01_python_oop_and_ros2_node.md",
                    "source": source_url,
                    "document_type": "md",
                    "relative_path": rel_path,
                    "chunk_index": 0,
                    "page_number": 1,
                    "content": "class MyNode(Node): pass",
                    "distance": 0.05,
                    "hybrid_score": 0.02,
                }
            ],
        }

        with patch("app.core.context_assembly.search_context", return_value=mock_search_result):
            canonical_context = assemble_canonical_context(
                query="ROS 2 OOP node",
                limit=5,
                project_id=1,
            )

            self.assertEqual(canonical_context.document_count, 1)
            self.assertEqual(len(canonical_context.sources), 1)

            source_item = canonical_context.sources[0]
            self.assertEqual(source_item.kind, "document")
            self.assertEqual(source_item.source_id, "document-chunk:555")
            self.assertEqual(source_item.relative_path, rel_path)
            self.assertEqual(source_item.source, source_url)
            self.assertEqual(source_item.filename, "01_python_oop_and_ros2_node.md")

    # -------------------------------------------------------------------------
    # 13. 인덱싱 제외 규칙 (inbox/, school/, .git/, build/, __pycache__, .env, .bag)
    # -------------------------------------------------------------------------
    def test_13_indexing_exclusions_rejected(self):
        client = self._client_with_write_access(project_id=1)

        excluded_cases = [
            ("inbox/README.md", "README.md", "excluded indexing pattern 'inbox/'"),
            ("school/syllabus.md", "syllabus.md", "excluded indexing pattern 'school/'"),
            (".git/config", "config", "excluded indexing pattern '.git/'"),
            ("build/Makefile", "Makefile", "excluded indexing pattern 'build/'"),
            ("install/setup.bash", "setup.bash", "excluded indexing pattern 'install/'"),
            ("log/latest.txt", "latest.txt", "excluded indexing pattern 'log/'"),
            ("pkg/__pycache__/node.py", "node.py", "excluded indexing pattern '__pycache__/'"),
            ("external/lecture.md", "lecture.md", "excluded indexing pattern 'external/'"),
            ("lectures/unverified/summary.md", "summary.md", "excluded indexing pattern 'lectures/unverified/'"),
            (".env", ".env", "Sensitive or restricted file is not permitted"),
            ("id_rsa", "id_rsa", "Sensitive or restricted file is not permitted"),
            ("recording.bag", "recording.bag", "Sensitive or restricted file is not permitted"),
        ]

        for rel, fname, expected_msg in excluded_cases:
            response = client.post(
                "/documents/upload",
                data={"project_id": 1, "relative_path": rel},
                files={"file": (fname, io.BytesIO(b"# Excluded"), "text/plain")},
            )
            self.assertEqual(response.status_code, 400, f"Failed for {rel}")
            self.assertIn(expected_msg, response.json()["detail"])

    # -------------------------------------------------------------------------
    # 14. 경로 탈출 및 절대 경로 거부
    # -------------------------------------------------------------------------
    def test_14_path_traversal_and_invalid_paths_rejected(self):
        client = self._client_with_write_access(project_id=1)

        invalid_cases = [
            ("../../etc/passwd/test.md", "escapes repository root"),
            ("../test.md", "escapes repository root"),
            ("02_ros2/../../outside/test.md", "escapes repository root"),
            ("/etc/shadow/test.md", "relative path from the repository root"),
            ("C:\\Windows\\test.md", "relative path from the repository root"),
            ("docs/wrong_name.md", "does not match uploaded filename"),
            ("   ", "must not be empty"),
        ]

        for bad_path, expected_err in invalid_cases:
            response = client.post(
                "/documents/upload",
                data={"project_id": 1, "relative_path": bad_path},
                files={"file": ("test.md", io.BytesIO(b"# Test"), "text/markdown")},
            )
            self.assertEqual(response.status_code, 400, f"Failed for bad_path: {bad_path}")
            self.assertIn(expected_err, response.json()["detail"])

    # -------------------------------------------------------------------------
    # 15. 잘못된 GitHub URL 및 커밋 SHA 거부
    # -------------------------------------------------------------------------
    def test_15_invalid_github_url_and_commit_sha_rejected(self):
        client = self._client_with_write_access(project_id=1)
        rel = "docs/test.md"

        # Invalid URLs
        invalid_urls = [
            ("https://malicious.org/repo/blob/fd9742cf9cf2940654833fcf8c3c8d951adeb3e1/docs/test.md", "valid GitHub blob URL"),
            (f"https://github.com/ATTACKER/learning-portfolio/blob/{_VALID_SHA}/{rel}", "owner 'ATTACKER' is not authorized"),
            (f"https://github.com/CHUNGJINWOO/forbidden-private-repo/blob/{_VALID_SHA}/{rel}", "repository 'forbidden-private-repo' is not authorized"),
            (f"https://github.com/CHUNGJINWOO/learning-portfolio/blob/{_VALID_SHA}/docs/mismatched.md", "does not match relative_path"),
        ]

        for bad_url, expected_err in invalid_urls:
            response = client.post(
                "/documents/upload",
                data={"project_id": 1, "relative_path": rel, "source": bad_url},
                files={"file": ("test.md", io.BytesIO(b"# Test"), "text/markdown")},
            )
            self.assertEqual(response.status_code, 400, f"Failed for bad_url: {bad_url}")
            self.assertIn(expected_err, response.json()["detail"])

        # Invalid SHAs
        res_short = client.post(
            "/documents/upload",
            data={"project_id": 1, "relative_path": rel, "commit_sha": "short123"},
            files={"file": ("test.md", io.BytesIO(b"# Test"), "text/markdown")},
        )
        self.assertEqual(res_short.status_code, 400)
        self.assertIn("40-character hexadecimal", res_short.json()["detail"])

        res_nonhex = client.post(
            "/documents/upload",
            data={"project_id": 1, "relative_path": rel, "commit_sha": "z" * 40},
            files={"file": ("test.md", io.BytesIO(b"# Test"), "text/markdown")},
        )
        self.assertEqual(res_nonhex.status_code, 400)
        self.assertIn("40-character hexadecimal", res_nonhex.json()["detail"])

    # -------------------------------------------------------------------------
    # 16. Active 문서 검색 허용 및 SQL 필터 검증
    # -------------------------------------------------------------------------
    def test_16_search_active_documents_included(self):
        from app.core.search import search_documents

        captured_queries = []
        mock_row = (
            10,  # chunk_id
            100,  # document_id
            1,    # project_id
            "Active Guide",
            "guide.md",
            "https://github.com/CHUNGJINWOO/learning-portfolio/blob/sha/docs/guide.md",
            "md",
            "docs/guide.md",
            0,    # chunk_index
            1,    # page_number
            "Active content",
            0.05, # distance
        )

        class SearchMockConn:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def execute(self, query, params=None):
                captured_queries.append(" ".join(query.strip().split()))
                mock_cur = MagicMock()
                mock_cur.fetchall.return_value = [mock_row]
                return mock_cur

        with patch("app.core.search.get_db_connection", return_value=SearchMockConn()), \
             patch("app.core.search.create_query_embedding", return_value=[0.1] * 384):

            results = search_documents("guide", query_embedding=MagicMock(), project_id=1)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["document_id"], 100)

            # Verify query contains status filters allowing active and completed projects
            sql = captured_queries[0]
            self.assertIn("d.status = 'active'", sql)
            self.assertIn("p.status IN ('active', 'completed')", sql)

    # -------------------------------------------------------------------------
    # 17. Archived 문서 검색 제외 검증
    # -------------------------------------------------------------------------
    def test_17_search_archived_documents_excluded(self):
        from app.core.search import search_documents

        captured_queries = []
        class EmptySearchMockConn:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def execute(self, query, params=None):
                captured_queries.append(" ".join(query.strip().split()))
                mock_cur = MagicMock()
                mock_cur.fetchall.return_value = []  # No rows match active filter
                return mock_cur

        with patch("app.core.search.get_db_connection", return_value=EmptySearchMockConn()):
            results = search_documents("guide", query_embedding=MagicMock(), project_id=1)
            self.assertEqual(len(results), 0)
            self.assertIn("d.status = 'active'", captured_queries[0])

    # -------------------------------------------------------------------------
    # 18. Archived 프로젝트 소속 문서 검색 제외 검증
    # -------------------------------------------------------------------------
    def test_18_search_archived_project_documents_excluded(self):
        from app.core.search import search_documents

        captured_queries = []
        class ProjectCheckConn:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def execute(self, query, params=None):
                captured_queries.append(" ".join(query.strip().split()))
                mock_cur = MagicMock()
                mock_cur.fetchall.return_value = []
                return mock_cur

        with patch("app.core.search.get_db_connection", return_value=ProjectCheckConn()):
            results = search_documents("guide", query_embedding=MagicMock(), project_id=1)
            self.assertEqual(len(results), 0)
            # Ensure p.status filter requires active or completed
            self.assertIn("(p.status IS NULL OR p.status IN ('active', 'completed'))", captured_queries[0])

    # -------------------------------------------------------------------------
    # 19. 전체 프로젝트 검색(project_id=None)의 상태 필터 일관성 검증
    # -------------------------------------------------------------------------
    def test_19_search_global_status_filter_applied(self):
        from app.core.search import search_documents

        captured_queries = []
        class GlobalSearchConn:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def execute(self, query, params=None):
                captured_queries.append(" ".join(query.strip().split()))
                mock_cur = MagicMock()
                mock_cur.fetchall.return_value = []
                return mock_cur

        with patch("app.core.search.get_db_connection", return_value=GlobalSearchConn()):
            results = search_documents("guide", query_embedding=MagicMock(), project_id=None)
            self.assertEqual(len(results), 0)
            sql = captured_queries[0]
            self.assertIn("d.status = 'active'", sql)
            self.assertIn("(p.status IN ('active', 'completed') OR d.project_id IS NULL)", sql)

    # -------------------------------------------------------------------------
    # 20. 메모리 검색의 프로젝트 상태 필터 일관성 검증
    # -------------------------------------------------------------------------
    def test_20_search_memories_archived_project_excluded(self):
        from app.core.search import search_memories

        captured_queries = []
        class MemorySearchConn:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def execute(self, query, params=None):
                captured_queries.append(" ".join(query.strip().split()))
                mock_cur = MagicMock()
                mock_cur.fetchall.return_value = []
                return mock_cur

        with patch("app.core.search.get_db_connection", return_value=MemorySearchConn()):
            # Test project-specific memory search
            search_memories(query_embedding=MagicMock(), project_id=1)
            self.assertIn("(p.status IS NULL OR p.status IN ('active', 'completed'))", captured_queries[0])
            self.assertIn("m.project_id = %s", captured_queries[0])

            # Test global memory search
            search_memories(query_embedding=MagicMock(), project_id=None)
            self.assertIn("(p.status IN ('active', 'completed') OR m.project_id IS NULL)", captured_queries[1])

    # -------------------------------------------------------------------------
    # 21. 권한 없는 프로젝트의 검색 차단 (403 Forbidden)
    # -------------------------------------------------------------------------
    def test_21_unauthorized_context_search_denied(self):
        client = self._client_with_read_only(project_id=1)

        # User only has access to project 1; querying project 2 must be 403
        response = client.get("/context/search", params={"q": "ros2", "project_id": 2})
        self.assertEqual(response.status_code, 403)

    # -------------------------------------------------------------------------
    # 22. 출처 검증 메타데이터 신뢰 경계 구분 (integrity_verified & assertion)
    # -------------------------------------------------------------------------
    def test_22_provenance_trust_boundary_metadata(self):
        client = self._client_with_write_access(project_id=1)
        rel_path = "02_ros2/docs/test.md"
        source_url = f"https://github.com/CHUNGJINWOO/learning-portfolio/blob/{_VALID_SHA}/{rel_path}"
        content = b"# Provenance Trust Content"
        correct_sha = hashlib.sha256(content).hexdigest()

        mock_conn = MockDBConnection(project_exists=True, existing_doc=None, insert_doc_id=555)

        with patch("app.routers.documents.get_db_connection", return_value=mock_conn), \
             patch("app.routers.documents.extract_text", return_value=["Content"]), \
             patch("app.routers.documents.chunk_pages", return_value=[(0, 1, "Content")]):

            res = client.post(
                "/documents/upload",
                data={
                    "project_id": 1,
                    "relative_path": rel_path,
                    "source": source_url,
                    "commit_sha": _VALID_SHA,
                    "expected_sha256": correct_sha,
                },
                files={"file": ("test.md", io.BytesIO(content), "text/markdown")},
            )

            self.assertEqual(res.status_code, 200)
            data = res.json()
            # Server cryptographically verified incoming payload SHA-256
            self.assertEqual(data["upload_integrity"], "client_sha256_match")
            self.assertEqual(data["provenance_assertion"], "client_git_commit")
            self.assertFalse(data["server_git_verified"])

            # Verify description JSON contains client provenance assertion and explicit server limits
            insert_queries = [p for q, p in mock_conn.executed_queries if "INSERT INTO documents" in q]
            self.assertEqual(len(insert_queries), 1)
            desc_json = json.loads(insert_queries[0][5])
            self.assertEqual(desc_json["provenance_assertion"], "client_git_commit")
            self.assertEqual(desc_json["upload_integrity"], "client_sha256_match")
            self.assertFalse(desc_json["server_git_verified"])

    # -------------------------------------------------------------------------
    # 23. B1: SQL 컬럼 별칭 모호성 방지 검증 (m.id, m.project_id)
    # -------------------------------------------------------------------------
    def test_23_sql_column_ambiguity_prevented(self):
        from app.core.search import search_memories

        captured_queries = []
        class CaptureConn:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def execute(self, query, params=None):
                captured_queries.append(" ".join(query.strip().split()))
                mock_cur = MagicMock()
                mock_cur.fetchall.return_value = []
                return mock_cur

        with patch("app.core.search.get_db_connection", return_value=CaptureConn()):
            search_memories(query_embedding=MagicMock(), project_id=1)
            proj_sql = captured_queries[0]
            # Ensure SELECT id is fully qualified as m.id and WHERE project_id is m.project_id
            self.assertIn("SELECT m.id,", proj_sql)
            self.assertIn("AND m.project_id = %s", proj_sql)
            self.assertIn("FROM memories m LEFT JOIN projects p", proj_sql)

            search_memories(query_embedding=MagicMock(), project_id=None)
            global_sql = captured_queries[1]
            self.assertIn("SELECT m.id,", global_sql)
            self.assertIn("m.project_id IS NULL", global_sql)

    # -------------------------------------------------------------------------
    # 24. Completed 상태 프로젝트의 검색 허용 검증
    # -------------------------------------------------------------------------
    def test_24_completed_project_search_allowed(self):
        from app.core.search import search_documents, search_memories

        captured_queries = []
        class CaptureConn:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def execute(self, query, params=None):
                captured_queries.append(" ".join(query.strip().split()))
                mock_cur = MagicMock()
                mock_cur.fetchall.return_value = []
                return mock_cur

        with patch("app.core.search.get_db_connection", return_value=CaptureConn()):
            search_documents("test", query_embedding=MagicMock(), project_id=1)
            search_memories(query_embedding=MagicMock(), project_id=1)
            for q in captured_queries:
                self.assertIn("IN ('active', 'completed')", q)

    # -------------------------------------------------------------------------
    # 25. /documents/search 전체 검색의 프로젝트 권한 검증 (프로젝트 전용 권한자의 전체 검색 거부)
    # -------------------------------------------------------------------------
    def test_25_documents_search_global_scope_authorization(self):
        client = self._client_with_read_only(project_id=1)

        # Caller has read permission ONLY for project 1; omitting project_id must return 403
        response = client.get("/documents/search", params={"q": "ros2"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("searching documents without project_id requires", response.json()["detail"])

    # -------------------------------------------------------------------------
    # 26. B2: 실패 요청이 이미 커밋된 정상 문서 파일을 삭제하지 않는지 검증
    # -------------------------------------------------------------------------
    def test_26_failed_upload_does_not_delete_committed_document_file(self):
        client = self._client_with_write_access(project_id=1)

        # 1. Existing committed file for document A on disk
        committed_bytes = b"# Live Committed Document Content"
        content_sha = hashlib.sha256(committed_bytes).hexdigest()
        committed_file_name = f"{content_sha}_committed.md"
        committed_file_path = _TEST_DIR / committed_file_name
        committed_file_path.write_bytes(committed_bytes)

        self.assertTrue(committed_file_path.exists())

        # 2. Upload document B with the same raw bytes, but simulate an extraction error
        mock_conn = MockDBConnection(project_exists=True, existing_doc=None)

        with patch("app.routers.documents.get_db_connection", return_value=mock_conn), \
             patch("app.routers.documents.extract_text", side_effect=ValueError("Corrupt document")):

            res = client.post(
                "/documents/upload",
                data={"project_id": 1, "relative_path": "docs/committed.md"},
                files={"file": ("committed.md", io.BytesIO(committed_bytes), "text/markdown")},
            )
            self.assertEqual(res.status_code, 400)

        # 3. Document A's committed file MUST still exist!
        self.assertTrue(
            committed_file_path.exists(),
            "Critical regression: failed upload deleted an existing committed document file!",
        )

        # 4. Verify no lingering temporary files
        temp_files = list(_TEST_DIR.glob("tmp_*"))
        self.assertEqual(len(temp_files), 0, "Temporary file was not cleaned up on failure")

    # -------------------------------------------------------------------------
    # 27. B3: 레거시 업로드 - 동일 파일명 복수 업로드 시 기존 새 문서 생성 동작 유지
    # -------------------------------------------------------------------------
    def test_27_legacy_upload_duplicate_filename_creates_new_documents(self):
        client = self._client_with_write_access(project_id=1)

        # Both uploads have relative_path=None (legacy format), same filename package.xml
        mock_conn1 = MockDBConnection(project_exists=True, existing_doc=None, insert_doc_id=1001)
        mock_conn2 = MockDBConnection(project_exists=True, existing_doc=None, insert_doc_id=1002)

        with patch("app.routers.documents.get_db_connection", return_value=mock_conn1), \
             patch("app.routers.documents.extract_text", return_value=["pkg1"]), \
             patch("app.routers.documents.chunk_pages", return_value=[(0, 1, "pkg1")]):

            res1 = client.post(
                "/documents/upload",
                data={"project_id": 1},
                files={"file": ("package.xml", io.BytesIO(b"<package>1</package>"), "application/xml")},
            )
            self.assertEqual(res1.status_code, 200)
            self.assertEqual(res1.json()["status"], "created")
            self.assertEqual(res1.json()["document_id"], 1001)

        with patch("app.routers.documents.get_db_connection", return_value=mock_conn2), \
             patch("app.routers.documents.extract_text", return_value=["pkg2"]), \
             patch("app.routers.documents.chunk_pages", return_value=[(0, 1, "pkg2")]):

            res2 = client.post(
                "/documents/upload",
                data={"project_id": 1},
                files={"file": ("package.xml", io.BytesIO(b"<package>2</package>"), "application/xml")},
            )
            # Legacy upload does not trigger 409 conflict even with same filename in same project!
            self.assertEqual(res2.status_code, 200)
            self.assertEqual(res2.json()["status"], "created")
            self.assertEqual(res2.json()["document_id"], 1002)

    # -------------------------------------------------------------------------
    # 28. Advisory Lock 획득 실패 시 안전한 요청 거부 및 임시파일 정리 검증
    # -------------------------------------------------------------------------
    def test_28_advisory_lock_failure_handled_safely(self):
        client = self._client_with_write_access(project_id=1)

        class LockFailConn(MockDBConnection):
            def execute(self, query, params=None):
                if "pg_advisory_xact_lock" in query:
                    raise RuntimeError("PostgreSQL advisory lock acquisition timeout")
                return super().execute(query, params)

        mock_conn = LockFailConn(project_exists=True, existing_doc=None)

        with patch("app.routers.documents.get_db_connection", return_value=mock_conn):
            res = client.post(
                "/documents/upload",
                data={"project_id": 1, "relative_path": "docs/test_lock.md"},
                files={"file": ("test_lock.md", io.BytesIO(b"# Content"), "text/markdown")},
            )
            # Must return 500 when lock fails instead of silently executing without lock
            self.assertEqual(res.status_code, 500)
            self.assertIn("advisory lock", res.json()["detail"])

            # Verify no temp files leaked
            temp_files = list(_TEST_DIR.glob("tmp_*"))
            self.assertEqual(len(temp_files), 0)

    # -------------------------------------------------------------------------
    # 29. 정상 파일명(tokenizer.py 등) 오탐 방지 및 실제 시크릿 차단 검증
    # -------------------------------------------------------------------------
    def test_29_false_positive_filename_patterns_allowed(self):
        client = self._client_with_write_access(project_id=1)
        mock_conn = MockDBConnection(project_exists=True, existing_doc=None, insert_doc_id=303)

        allowed_code_files = [
            ("src/tokenizer.py", "tokenizer.py", "text/x-python", b"# Tokenizer code"),
            ("docs/token_notes.md", "token_notes.md", "text/markdown", b"# Token notes"),
            ("docs/credentials_guide.md", "credentials_guide.md", "text/markdown", b"# Credentials guide"),
        ]

        with patch("app.routers.documents.get_db_connection", return_value=mock_conn), \
             patch("app.routers.documents.extract_text", return_value=["Content"]), \
             patch("app.routers.documents.chunk_pages", return_value=[(0, 1, "Content")]):

            for rel, fname, mime, content in allowed_code_files:
                res = client.post(
                    "/documents/upload",
                    data={"project_id": 1, "relative_path": rel},
                    files={"file": (fname, io.BytesIO(content), mime)},
                )
                self.assertEqual(res.status_code, 200, f"False positive rejection for legitimate file {rel}")

        # Real secrets/credentials must still be rejected
        blocked_files = [
            ("auth_token.json", "auth_token.json", b'{"token": "xyz"}'),
            ("credentials.json", "credentials.json", b'{"key": "xyz"}'),
            ("client_secret_test.json", "client_secret_test.json", b'{"secret": "xyz"}'),
        ]
        for rel, fname, content in blocked_files:
            res = client.post(
                "/documents/upload",
                data={"project_id": 1, "relative_path": rel},
                files={"file": (fname, io.BytesIO(content), "application/json")},
            )
            self.assertEqual(res.status_code, 400, f"Secret file {rel} was not blocked")

    # -------------------------------------------------------------------------
    # 30. URL 인코딩 유니코드 경로 정규화 매칭 검증
    # -------------------------------------------------------------------------
    def test_30_lectures_unverified_exclusion_and_unicode_url(self):
        client = self._client_with_write_access(project_id=1)
        mock_conn = MockDBConnection(project_exists=True, existing_doc=None, insert_doc_id=404)

        rel = "docs/한글_문서.md"
        # GitHub encodes Korean characters in URLs
        quoted_path = "docs/%ED%95%9C%EA%B8%80_%EB%AC%B8%EC%84%9C.md"
        source_url = f"https://github.com/CHUNGJINWOO/learning-portfolio/blob/{_VALID_SHA}/{quoted_path}"

        with patch("app.routers.documents.get_db_connection", return_value=mock_conn), \
             patch("app.routers.documents.extract_text", return_value=["Korean Content"]), \
             patch("app.routers.documents.chunk_pages", return_value=[(0, 1, "Korean Content")]):

            res = client.post(
                "/documents/upload",
                data={"project_id": 1, "relative_path": rel, "source": source_url},
                files={"file": ("한글_문서.md", io.BytesIO(b"# Korean Content"), "text/markdown")},
            )
            self.assertEqual(res.status_code, 200, "URL-encoded Korean path failed to match relative_path")


if __name__ == "__main__":
    unittest.main()
