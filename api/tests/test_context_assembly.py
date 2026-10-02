import unittest
from unittest.mock import MagicMock, patch

from app.core import context_assembly
from app.core import search as search_core
from app.routers import context as context_router
from fastapi import HTTPException
from pydantic import ValidationError


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

    def test_malformed_search_result_is_rejected(self):
        with patch.object(
            context_assembly,
            "search_context",
            return_value={"query": "topic", "memories": []},
        ):
            with self.assertRaisesRegex(
                ValueError,
                "search_context result is missing: documents",
            ):
                context_assembly.assemble_canonical_context("topic")

    def test_non_mapping_search_result_is_rejected(self):
        with patch.object(
            context_assembly,
            "search_context",
            return_value=None,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "search_context must return a mapping",
            ):
                context_assembly.assemble_canonical_context("topic")

    def test_result_item_without_required_provenance_is_rejected(self):
        with patch.object(
            context_assembly,
            "search_context",
            return_value={
                "query": "topic",
                "memories": [{"content": "memory"}],
                "documents": [],
            },
        ):
            with self.assertRaisesRegex(
                ValueError,
                "search_context result item is missing id: memories",
            ):
                context_assembly.assemble_canonical_context("topic")

    def test_non_list_memories_collection_is_rejected(self):
        with patch.object(
            context_assembly,
            "search_context",
            return_value={
                "query": "topic",
                "memories": {},
                "documents": [],
            },
        ):
            with self.assertRaisesRegex(
                ValueError,
                "search_context result field must be a list: memories",
            ):
                context_assembly.assemble_canonical_context("topic")

    def test_non_list_documents_collection_is_rejected(self):
        with patch.object(
            context_assembly,
            "search_context",
            return_value={
                "query": "topic",
                "memories": [],
                "documents": {},
            },
        ):
            with self.assertRaisesRegex(
                ValueError,
                "search_context result field must be a list: documents",
            ):
                context_assembly.assemble_canonical_context("topic")

    def test_non_mapping_search_result_item_is_rejected(self):
        with patch.object(
            context_assembly,
            "search_context",
            return_value={
                "query": "topic",
                "memories": ["memory"],
                "documents": [],
            },
        ):
            with self.assertRaisesRegex(
                ValueError,
                "search_context result items must be mappings: memories",
            ):
                context_assembly.assemble_canonical_context("topic")

    def test_document_without_chunk_id_is_rejected(self):
        with patch.object(
            context_assembly,
            "search_context",
            return_value={
                "query": "topic",
                "memories": [],
                "documents": [{"content": "document"}],
            },
        ):
            with self.assertRaisesRegex(
                ValueError,
                "search_context result item is missing chunk_id: documents",
            ):
                context_assembly.assemble_canonical_context("topic")

    def test_document_without_content_is_rejected(self):
        with patch.object(
            context_assembly,
            "search_context",
            return_value={
                "query": "topic",
                "memories": [],
                "documents": [{"chunk_id": 4}],
            },
        ):
            with self.assertRaisesRegex(
                ValueError,
                "search_context result item is missing content: documents",
            ):
                context_assembly.assemble_canonical_context("topic")

    def test_duplicate_item_id_is_rejected(self):
        source = {
            "source_id": "memory:8",
            "kind": "memory",
        }
        with self.assertRaisesRegex(
            ValueError,
            "item_id values must be unique",
        ):
            context_assembly.CanonicalContext(
                query="query",
                memory_count=2,
                document_count=0,
                items=[
                    {
                        "item_id": "memory:8",
                        "kind": "memory",
                        "content": "first",
                        "source_id": "memory:8",
                        "metadata": {},
                    },
                    {
                        "item_id": "memory:8",
                        "kind": "memory",
                        "content": "second",
                        "source_id": "memory:8",
                        "metadata": {},
                    },
                ],
                sources=[source],
            )

    def test_duplicate_source_id_is_rejected(self):
        item = {
            "item_id": "memory:8",
            "kind": "memory",
            "content": "memory",
            "source_id": "memory:8",
            "metadata": {},
        }
        with self.assertRaisesRegex(
            ValueError,
            "source_id values must be unique",
        ):
            context_assembly.CanonicalContext(
                query="query",
                memory_count=1,
                document_count=0,
                items=[item],
                sources=[
                    {
                        "source_id": "memory:8",
                        "kind": "memory",
                    },
                    {
                        "source_id": "memory:8",
                        "kind": "memory",
                    },
                ],
            )

    def test_count_mismatch_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "memory_count does not match context items",
        ):
            context_assembly.CanonicalContext(
                query="query",
                memory_count=2,
                document_count=0,
                items=[
                    {
                        "item_id": "memory:8",
                        "kind": "memory",
                        "content": "memory",
                        "source_id": "memory:8",
                        "metadata": {},
                    }
                ],
                sources=[
                    {
                        "source_id": "memory:8",
                        "kind": "memory",
                    }
                ],
            )

    def test_project_filter_passes_through_to_shared_search(self):
        with patch.object(context_assembly, "search_context", return_value={
            "query": "x", "project_id": 2, "memories": [], "documents": []
        }) as search:
            context_assembly.assemble_context("x", project_id=2)
        search.assert_called_once_with(query="x", limit=5, project_id=2)

    def test_canonical_context_uses_uniform_items_and_linked_sources(self):
        search_result = {
            "query": "shared context",
            "project_id": 2,
            "memories": [{
                "id": 8,
                "content": "Memory content",
                "project_id": 2,
                "distance": 0.12,
            }],
            "documents": [{
                "chunk_id": 31,
                "document_id": 7,
                "project_id": 2,
                "content": "Document content",
                "filename": "guide.md",
                "relative_path": "docs/guide.md",
                "chunk_index": 3,
                "distance": 0.08,
                "hybrid_score": -0.42,
            }],
        }
        with patch.object(context_assembly, "search_context", return_value=search_result):
            context = context_assembly.assemble_canonical_context(
                "shared context",
                project_id=2,
            )

        self.assertEqual(context.context_schema_version, "1")
        self.assertEqual(context.memory_count, 1)
        self.assertEqual(context.document_count, 1)
        self.assertEqual(
            [item.item_id for item in context.items],
            ["memory:8", "document-chunk:31"],
        )
        self.assertEqual(
            set(context.items[0].model_dump()),
            set(context.items[1].model_dump()),
        )
        sources_by_id = {
            source.source_id: source for source in context.sources
        }
        self.assertEqual(
            {item.source_id for item in context.items},
            set(sources_by_id),
        )
        self.assertEqual(
            sources_by_id["document-chunk:31"].relative_path,
            "docs/guide.md",
        )

    def test_canonical_context_rejects_items_without_matching_sources(self):
        with self.assertRaises(ValidationError):
            context_assembly.CanonicalContext(
                query="query",
                memory_count=1,
                document_count=0,
                items=[{
                    "item_id": "memory:8",
                    "kind": "memory",
                    "content": "Memory content",
                    "source_id": "memory:8",
                    "metadata": {},
                }],
                sources=[],
            )

    def test_canonical_context_supports_empty_results(self):
        with patch.object(context_assembly, "search_context", return_value={
            "query": "no matches",
            "project_id": None,
            "memories": [],
            "documents": [],
        }):
            context = context_assembly.assemble_canonical_context("no matches")

        self.assertEqual(context.items, [])
        self.assertEqual(context.sources, [])
        self.assertEqual(context.memory_count, 0)
        self.assertEqual(context.document_count, 0)

    def test_legacy_assembly_shape_does_not_expose_canonical_fields(self):
        with patch.object(context_assembly, "search_context", return_value={
            "query": "empty",
            "project_id": None,
            "memories": [],
            "documents": [],
        }):
            result = context_assembly.assemble_context("empty")

        self.assertEqual(
            set(result),
            {
                "query",
                "project_id",
                "memory_count",
                "document_count",
                "memories",
                "documents",
                "sources",
            },
        )
        self.assertEqual(result["memory_count"], 0)
        self.assertEqual(result["document_count"], 0)
        self.assertEqual(result["memories"], [])
        self.assertEqual(result["documents"], [])
        self.assertEqual(result["sources"], [])

    def test_empty_query_route_returns_400(self):
        with self.assertRaises(HTTPException) as raised:
            context_router.assemble_context(q=" ")
        self.assertEqual(raised.exception.status_code, 400)

    def test_assembly_route_returns_normalized_response(self):
        response = {
            "query": "query",
            "project_id": 2,
            "memory_count": 1,
            "document_count": 0,
            "memories": [{"item_id": "memory:8"}],
            "documents": [],
            "sources": [{"source_id": "memory:8"}],
        }
        with patch.object(
            context_router,
            "build_context",
            return_value=response,
        ) as build_context:
            result = context_router.assemble_context(
                q="query",
                limit=3,
                project_id=2,
            )

        build_context.assert_called_once_with(
            query="query",
            limit=3,
            project_id=2,
        )
        self.assertEqual(result, response)

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

    def test_assembly_route_keeps_legacy_response_shape(self):
        original_response = {
            "query": "query",
            "project_id": 2,
            "memory_count": 1,
            "document_count": 1,
            "memories": [{"item_id": "memory:8"}],
            "documents": [{"item_id": "document-chunk:31"}],
            "sources": [
                {"source_id": "memory:8"},
                {"source_id": "document-chunk:31"},
            ],
        }
        with patch.object(
            context_router,
            "build_context",
            return_value=original_response,
        ):
            response = context_router.assemble_context(
                q="query",
                project_id=2,
            )

        self.assertEqual(response, original_response)

    def test_search_contract_keeps_memory_and_document_results_scoped(self):
        memory = {"id": 8, "content": "memory", "project_id": 2}
        document = {"chunk_id": 31, "document_id": 7, "project_id": 2}
        with (
            patch.object(search_core, "create_query_embedding", return_value="vector"),
            patch.object(search_core, "search_memories", return_value=[memory]) as memories,
            patch.object(search_core, "search_documents", return_value=[document]) as documents,
        ):
            result = search_core.search_context(
                query="query",
                limit=4,
                project_id=2,
            )

        memories.assert_called_once_with(
            query_embedding="vector",
            limit=4,
            project_id=2,
        )
        documents.assert_called_once_with(
            query="query",
            query_embedding="vector",
            limit=4,
            project_id=2,
        )
        self.assertEqual(result["memories"], [memory])
        self.assertEqual(result["documents"], [document])
        self.assertEqual(result["memory_count"], 1)
        self.assertEqual(result["document_count"], 1)

    def test_search_contract_returns_empty_collections(self):
        with (
            patch.object(search_core, "create_query_embedding", return_value="vector"),
            patch.object(search_core, "search_memories", return_value=[]),
            patch.object(search_core, "search_documents", return_value=[]),
        ):
            result = search_core.search_context(query="query")

        self.assertEqual(result["memories"], [])
        self.assertEqual(result["documents"], [])
        self.assertEqual(result["memory_count"], 0)
        self.assertEqual(result["document_count"], 0)

    def test_search_queries_apply_project_filter_before_ranking(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.return_value.fetchall.return_value = []

        with patch.object(search_core, "get_db_connection", return_value=connection):
            search_core.search_memories(query_embedding="vector", project_id=2)
            memory_query, memory_params = connection.execute.call_args.args
            self.assertIn("AND project_id = %s", memory_query)
            self.assertEqual(memory_params[1], 2)

            connection.execute.reset_mock()
            search_core.search_documents(
                query="query",
                query_embedding="vector",
                project_id=2,
            )
            document_query, document_params = connection.execute.call_args.args
            self.assertIn("AND d.project_id = %s", document_query)
            self.assertEqual(document_params[1], 2)


if __name__ == "__main__":
    unittest.main()
