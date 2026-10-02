import asyncio
import os
from pathlib import Path
import unittest
from urllib.parse import unquote, urlsplit
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

os.environ.setdefault("MCP_ACCESS_TOKEN", "test-only-mcp-token")

from app.core.context_assembly import assemble_canonical_context
from app.core.search import search_context
from app.main import app

from app.mcp_server import mcp


DSN = os.getenv("AIHUB_CONTEXT_E2E_DSN")
DISPOSABLE_MARKER = "AIHUB_CONTEXT_E2E_ALLOW_DISPOSABLE"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _validated_connection_settings(dsn: str) -> dict[str, str]:
    if os.getenv(DISPOSABLE_MARKER) != "1":
        raise RuntimeError(
            f"{DISPOSABLE_MARKER}=1 is required for destructive E2E setup"
        )

    parsed = urlsplit(dsn)
    if parsed.scheme not in {"postgresql", "postgres"}:
        raise RuntimeError("E2E DSN must use the postgresql scheme")
    if parsed.hostname not in LOCAL_HOSTS:
        raise RuntimeError("E2E DSN host must be local-only")
    if not parsed.username or not parsed.password:
        raise RuntimeError("E2E DSN must include a test user and password")

    database = parsed.path.removeprefix("/")
    if not database.startswith("aihub_context_e2e"):
        raise RuntimeError(
            "E2E DSN database must use the aihub_context_e2e test prefix"
        )
    if not parsed.username.startswith("e2e_user"):
        raise RuntimeError("E2E DSN user must use the e2e_user test prefix")

    return {
        "POSTGRES_HOST": parsed.hostname,
        "POSTGRES_PORT": str(parsed.port or 5432),
        "POSTGRES_DB": database,
        "POSTGRES_USER": unquote(parsed.username),
        "POSTGRES_PASSWORD": unquote(parsed.password),
    }


class ContextPostgresSafetyTests(unittest.TestCase):
    def test_unsafe_remote_dsn_is_rejected_without_connecting(self):
        with patch.dict(
            os.environ,
            {DISPOSABLE_MARKER: "1"},
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "local-only"):
                _validated_connection_settings(
                    "postgresql://e2e_user:e2e_password@db.example/"
                    "aihub_context_e2e"
                )

    def test_missing_disposable_marker_is_rejected(self):
        with patch.dict(
            os.environ,
            {DISPOSABLE_MARKER: ""},
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "required"):
                _validated_connection_settings(
                    "postgresql://e2e_user:e2e_password@127.0.0.1/"
                    "aihub_context_e2e"
                )


class ContextPostgresE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not DSN:
            raise unittest.SkipTest(
                "AIHUB_CONTEXT_E2E_DSN is not configured"
            )

        cls.postgres_environment = _validated_connection_settings(DSN)
        cls.previous_environment = {
            key: os.environ.get(key)
            for key in cls.postgres_environment
        }
        cls.addClassCleanup(cls._restore_postgres_environment)
        os.environ.update(cls.postgres_environment)
        cls.connection_factory = staticmethod(
            lambda: psycopg.connect(DSN, row_factory=dict_row)
        )
        migration = (
            Path(__file__).parents[2] / "migrations" / "0001_baseline.sql"
        ).read_text()

        with cls.connection_factory() as connection:
            connection.execute("DROP SCHEMA public CASCADE")
            connection.execute("CREATE SCHEMA public")
            connection.execute(migration)
            project_ids = {}
            for slug in ("e2e-primary", "e2e-other"):
                project = connection.execute(
                    """
                    INSERT INTO public.projects (name, slug)
                    VALUES (%s, %s)
                    RETURNING id
                    """,
                    (slug, slug),
                ).fetchone()
                project_ids[slug] = project["id"]

            primary_id = project_ids["e2e-primary"]
            other_id = project_ids["e2e-other"]
            document = connection.execute(
                """
                INSERT INTO public.documents
                    (project_id, title, filename, document_type, relative_path)
                VALUES (%s, 'E2E guide', 'e2e-guide.md', 'markdown', 'docs/e2e-guide.md')
                RETURNING id
                """,
                (primary_id,),
            ).fetchone()

            vector = "[" + ",".join(["1"] + ["0"] * 383) + "]"
            connection.execute(
                """
                INSERT INTO public.memories
                    (content, memory_type, category, source, project_id, embedding)
                VALUES
                    ('E2E primary memory', 'fact', 'integration', 'e2e', %s, %s),
                    ('E2E other memory', 'fact', 'integration', 'e2e', %s, %s)
                """,
                (primary_id, vector, other_id, vector),
            )
            connection.execute(
                """
                INSERT INTO public.document_chunks
                    (document_id, chunk_index, content, embedding)
                VALUES (%s, 0, 'E2E primary document content', %s)
                """,
                (document["id"], vector),
            )
            connection.commit()

        cls.primary_id = primary_id

    @classmethod
    def _restore_postgres_environment(cls):
        for key, value in cls.previous_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    @classmethod
    def tearDownClass(cls):
        with cls.connection_factory() as connection:
            connection.execute("TRUNCATE document_chunks, documents, memories, projects CASCADE")
            connection.commit()

    def test_real_search_returns_fixture_memory_and_document(self):
        result = search_context(
            query="E2E primary",
            limit=1,
            project_id=self.primary_id,
        )

        self.assertEqual(result["query"], "E2E primary")
        self.assertEqual(result["project_id"], self.primary_id)
        self.assertLessEqual(len(result["memories"]), 1)
        self.assertLessEqual(len(result["documents"]), 1)
        self.assertTrue(
            any(item["content"] == "E2E primary memory" for item in result["memories"])
        )
        self.assertTrue(
            any(
                item["content"] == "E2E primary document content"
                for item in result["documents"]
            )
        )

    def test_real_search_assembles_canonical_context(self):
        context = assemble_canonical_context(
            query="E2E primary",
            limit=1,
            project_id=self.primary_id,
        )

        self.assertEqual(context.context_schema_version, "1")
        self.assertEqual(context.project_id, self.primary_id)
        self.assertEqual(context.memory_count, 1)
        self.assertEqual(context.document_count, 1)
        self.assertEqual(
            {item.source_id for item in context.items},
            {source.source_id for source in context.sources},
        )
        item_ids = {item.item_id for item in context.items}
        self.assertTrue(any(item_id.startswith("memory:") for item_id in item_ids))
        self.assertTrue(
            any(
                item_id.startswith("document-chunk:")
                for item_id in item_ids
            )
        )

    def test_real_http_routes_return_fixture_results(self):
        with TestClient(app) as client:
            assembled = client.get(
                "/context/assemble",
                params={
                    "q": "E2E primary",
                    "limit": 1,
                    "project_id": self.primary_id,
                },
            )
            searched = client.get(
                "/context/search",
                params={
                    "q": "E2E primary",
                    "limit": 1,
                    "project_id": self.primary_id,
                },
            )

        self.assertEqual(assembled.status_code, 200)
        assembled_json = assembled.json()
        self.assertEqual(assembled_json["query"], "E2E primary")
        self.assertEqual(assembled_json["project_id"], self.primary_id)
        self.assertEqual(assembled_json["memory_count"], 1)
        self.assertEqual(assembled_json["document_count"], 1)
        self.assertEqual(len(assembled_json["sources"]), 2)
        self.assertTrue(
            any(
                item["content"] == "E2E primary document content"
                for item in assembled_json["documents"]
            )
        )

        self.assertEqual(searched.status_code, 200)
        searched_json = searched.json()
        self.assertEqual(searched_json["project_id"], self.primary_id)
        self.assertTrue(
            all(
                item.get("project_id") == self.primary_id
                for item in searched_json["memories"]
            )
        )
        self.assertTrue(
            all(
                item.get("project_id") == self.primary_id
                for item in searched_json["documents"]
            )
        )

    def test_real_mcp_get_context_returns_project_scoped_canonical_context(self):
        result = asyncio.run(
            mcp.call_tool(
                "get_context",
                {
                    "query": "E2E primary",
                    "limit": 1,
                    "project_id": self.primary_id,
                },
            )
        )

        self.assertFalse(result.is_error)
        self.assertIsNotNone(result.structured_content)
        payload = result.structured_content
        self.assertEqual(payload["context_schema_version"], "1")
        self.assertEqual(payload["project_id"], self.primary_id)
        self.assertEqual(payload["memory_count"], 1)
        self.assertEqual(payload["document_count"], 1)
        self.assertTrue(
            all(
                source.get("project_id") == self.primary_id
                for source in payload["sources"]
            )
        )


if __name__ == "__main__":
    unittest.main()
