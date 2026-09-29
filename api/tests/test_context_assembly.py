import unittest
from unittest.mock import patch

from app.core import context_assembly
from app.routers import context as context_router
from fastapi import HTTPException


class ContextAssemblyTests(unittest.TestCase):
    def test_normalizes_items_and_preserves_citation_metadata(self):
        search_result = {
            "query": "cmd_vel publisher",
            "project_id": 2,
            "memory_count": 1,
            "document_count": 1,
            "memories": [{
                "id": 8,
                "content": "The driver publishes velocity commands.",
                "memory_type": "fact",
                "category": "architecture",
                "importance": 4,
                "source": "project notes",
                "project_id": 2,
                "distance": 0.12,
            }],
            "documents": [{
                "chunk_id": 31,
                "document_id": 7,
                "project_id": 2,
                "title": "LIMO driver",
                "filename": "limo_driver.cpp",
                "source": "src/limo_ros2/limo_base/src/limo_driver.cpp",
                "document_type": "code",
                "relative_path": "src/limo_ros2/limo_base/src/limo_driver.cpp",
                "chunk_index": 3,
                "page_number": None,
                "content": "Publisher code...",
                "distance": 0.08,
                "hybrid_score": -0.42,
            }],
        }
        with patch.object(context_assembly, "search_context", return_value=search_result) as search:
            result = context_assembly.assemble_context("cmd_vel publisher", project_id=2)

        search.assert_called_once_with(query="cmd_vel publisher", limit=5, project_id=2)
        self.assertEqual(result["project_id"], 2)
        self.assertEqual(result["memories"][0]["content"], search_result["memories"][0]["content"])
        self.assertEqual(result["documents"][0]["source_id"], "document-chunk:31")
        self.assertEqual(result["sources"][1]["document_id"], 7)
        self.assertEqual(result["sources"][1]["relative_path"], search_result["documents"][0]["relative_path"])
        self.assertEqual(result["sources"][1]["chunk_index"], 3)
        self.assertIsNone(result["sources"][1]["page_number"])
        self.assertEqual(result["sources"][1]["distance"], 0.08)
        self.assertEqual(result["sources"][1]["hybrid_score"], -0.42)

    def test_missing_provenance_stays_missing(self):
        search_result = {
            "query": "topic",
            "project_id": None,
            "memory_count": 0,
            "document_count": 1,
            "memories": [],
            "documents": [{"chunk_id": 4, "document_id": 9, "content": "text"}],
        }
        with patch.object(context_assembly, "search_context", return_value=search_result):
            source = context_assembly.assemble_context("topic")["sources"][0]
        self.assertIsNone(source["page_number"])
        self.assertIsNone(source["relative_path"])

    def test_project_filter_passes_through_to_shared_search(self):
        with patch.object(context_assembly, "search_context", return_value={
            "query": "x", "project_id": 2, "memories": [], "documents": []
        }) as search:
            context_assembly.assemble_context("x", project_id=2)
        search.assert_called_once_with(query="x", limit=5, project_id=2)

    def test_empty_query_route_returns_400(self):
        with self.assertRaises(HTTPException) as raised:
            context_router.assemble_context(q=" ")
        self.assertEqual(raised.exception.status_code, 400)

    def test_existing_search_route_keeps_unified_search_response(self):
        original_response = {
            "query": "cmd_vel publisher",
            "project_id": 2,
            "memory_count": 0,
            "document_count": 1,
            "memories": [],
            "documents": [{"chunk_id": 31, "document_id": 7}],
        }
        with patch.object(context_router, "unified_search_context", return_value=original_response):
            response = context_router.search_context(q="cmd_vel publisher", project_id=2)
        self.assertEqual(response, original_response)


if __name__ == "__main__":
    unittest.main()
