import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.routers import context as context_router


ASSEMBLED_RESPONSE = {
    "query": "cmd_vel publisher",
    "project_id": 2,
    "memory_count": 1,
    "document_count": 1,
    "memories": [
        {
            "item_id": "memory:8",
            "kind": "memory",
            "content": "The driver publishes velocity commands.",
            "source_id": "memory:8",
            "metadata": {"memory_type": "fact"},
        }
    ],
    "documents": [
        {
            "item_id": "document-chunk:31",
            "kind": "document",
            "content": "Publisher code...",
            "source_id": "document-chunk:31",
            "metadata": {"title": "LIMO driver"},
        }
    ],
    "sources": [
        {
            "source_id": "memory:8",
            "kind": "memory",
            "memory_id": 8,
            "project_id": 2,
        },
        {
            "source_id": "document-chunk:31",
            "kind": "document",
            "document_id": 7,
            "chunk_id": 31,
            "project_id": 2,
        },
    ],
}

SEARCH_RESPONSE = {
    "query": "cmd_vel publisher",
    "project_id": 2,
    "memory_count": 0,
    "document_count": 1,
    "memories": [],
    "documents": [{"chunk_id": 31, "document_id": 7}],
}


class ContextAssemblyHttpTests(unittest.TestCase):
    def client(self):
        return TestClient(app)

    def test_assemble_http_wiring_serializes_response_and_forwards_params(self):
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch.object(
                context_router,
                "build_context",
                return_value=ASSEMBLED_RESPONSE,
            ) as build_context,
        ):
            with self.client() as client:
                response = client.get(
                    "/context/assemble",
                    params={
                        "q": "cmd_vel publisher",
                        "limit": 3,
                        "project_id": 2,
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), ASSEMBLED_RESPONSE)
        build_context.assert_called_once_with(
            query="cmd_vel publisher",
            limit=3,
            project_id=2,
        )

    def test_assemble_http_rejects_empty_query(self):
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch.object(context_router, "build_context") as build_context,
        ):
            with self.client() as client:
                response = client.get("/context/assemble", params={"q": " "})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Query must not be empty")
        build_context.assert_not_called()

    def test_search_http_wiring_preserves_legacy_response_shape(self):
        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch.object(
                context_router,
                "unified_search_context",
                return_value=SEARCH_RESPONSE,
            ) as unified_search_context,
        ):
            with self.client() as client:
                response = client.get(
                    "/context/search",
                    params={
                        "q": "cmd_vel publisher",
                        "limit": 4,
                        "project_id": 2,
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), SEARCH_RESPONSE)
        unified_search_context.assert_called_once_with(
            query="cmd_vel publisher",
            limit=4,
            project_id=2,
        )


if __name__ == "__main__":
    unittest.main()
