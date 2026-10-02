import asyncio
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("MCP_ACCESS_TOKEN", "test-only-mcp-token")

from app.core.context_assembly import CanonicalContext
from app.mcp_server import get_context, mcp


class McpContextContractTests(unittest.TestCase):
    def test_registers_get_context_without_changing_existing_tools(self):
        tools = asyncio.run(mcp.list_tools())

        self.assertEqual(
            {tool.name for tool in tools},
            {
                "health_check",
                "search_context",
                "list_projects",
                "get_document",
                "get_context",
            },
        )
        get_context_tool = next(
            tool for tool in tools if tool.name == "get_context"
        )
        self.assertEqual(
            get_context_tool.input_schema["required"],
            ["query"],
        )
        self.assertEqual(
            set(get_context_tool.input_schema["properties"]),
            {"query", "limit", "project_id"},
        )
        self.assertEqual(
            set(get_context_tool.output_schema["properties"]),
            {
                "context_schema_version",
                "query",
                "project_id",
                "memory_count",
                "document_count",
                "items",
                "sources",
            },
        )

    def test_rejects_empty_query(self):
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            get_context("  ")

    def test_forwards_project_and_clamped_limit_to_canonical_capability(self):
        canonical = CanonicalContext(
            query="publisher",
            project_id=7,
            memory_count=0,
            document_count=0,
            items=[],
            sources=[],
        )

        with patch(
            "app.mcp_server.assemble_canonical_context",
            return_value=canonical,
        ) as assemble:
            result = get_context(
                query="publisher",
                limit=100,
                project_id=7,
            )

        self.assertIs(result, canonical)
        assemble.assert_called_once_with(
            query="publisher",
            limit=20,
            project_id=7,
        )

    def test_preserves_canonical_invariants_and_serialization(self):
        canonical = CanonicalContext(
            query="publisher",
            project_id=7,
            memory_count=1,
            document_count=0,
            items=[
                {
                    "item_id": "memory:1",
                    "kind": "memory",
                    "content": "publishes velocity",
                    "source_id": "memory:1",
                    "metadata": {},
                }
            ],
            sources=[
                {
                    "source_id": "memory:1",
                    "kind": "memory",
                    "project_id": 7,
                    "memory_id": 1,
                }
            ],
        )

        with patch(
            "app.mcp_server.assemble_canonical_context",
            return_value=canonical,
        ):
            result = get_context("publisher", project_id=7)

        payload = result.model_dump()
        self.assertEqual(payload["context_schema_version"], "1")
        self.assertEqual(payload["project_id"], 7)
        self.assertEqual(payload["memory_count"], 1)
        self.assertEqual(payload["document_count"], 0)
        self.assertEqual(
            {item["source_id"] for item in payload["items"]},
            {source["source_id"] for source in payload["sources"]},
        )

    def test_registered_tool_serializes_canonical_response(self):
        canonical = {
            "context_schema_version": "1",
            "query": "publisher",
            "project_id": 7,
            "memory_count": 0,
            "document_count": 0,
            "items": [],
            "sources": [],
        }

        with patch(
            "app.mcp_server.assemble_canonical_context",
            return_value=CanonicalContext.model_validate(canonical),
        ):
            result = asyncio.run(
                mcp.call_tool(
                    "get_context",
                    {"query": "publisher", "project_id": 7},
                )
            )

        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content, canonical)


if __name__ == "__main__":
    unittest.main()
