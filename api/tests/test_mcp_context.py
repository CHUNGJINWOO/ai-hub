import asyncio
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("MCP_ACCESS_TOKEN", "test-only-mcp-token")

from app.core.context_assembly import CanonicalContext
from app.core.authorization import (
    AuthorizationContext,
    mcp_authorization_context,
)
from app.mcp_server import get_context, list_skills, mcp


class McpContextContractTests(unittest.TestCase):
    def setUp(self):
        self.authorization_context = AuthorizationContext(
            identity="test-agent",
            allowed_project_ids=frozenset({7}),
            allow_global_read=True,
        )

    def test_registers_get_context_and_list_skills_while_preserving_existing_tools(self):
        tools = asyncio.run(mcp.list_tools())

        self.assertEqual(
            {tool.name for tool in tools},
            {
                "health_check",
                "search_context",
                "list_projects",
                "get_document",
                "get_context",
                "list_skills",
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

        list_skills_tool = next(
            tool for tool in tools if tool.name == "list_skills"
        )
        self.assertEqual(
            list_skills_tool.input_schema.get("required", []),
            [],
        )
        self.assertEqual(
            set(list_skills_tool.output_schema["properties"]),
            {"skills"},
        )

    def test_lists_skill_metadata_with_global_read_authorization(self):
        with mcp_authorization_context(self.authorization_context):
            result = list_skills()

        self.assertEqual(len(result.skills), 1)
        skill = result.skills[0]
        self.assertEqual(skill.skill_id, "ros2-robotics")
        self.assertEqual(skill.name, "ROS2 Robotics")
        self.assertEqual(skill.domains, ["ros2", "robotics"])
        self.assertEqual(
            skill.task_types,
            [
                "node",
                "topic",
                "service",
                "action",
                "parameter",
                "launch",
                "nav2",
            ],
        )
        self.assertEqual(
            skill.required_context,
            [
                "project-scoped canonical context",
                "source provenance",
                "project_id",
            ],
        )
        self.assertEqual(skill.input_schema, "SkillRequest")
        self.assertEqual(skill.output_schema, "SkillResult")
        self.assertEqual(skill.evidence_type, "ContextItem + ContextSource")
        self.assertEqual(skill.project_scope, "project-scoped")

    def test_permits_skill_listing_for_project_scoped_caller(self):
        authorization = AuthorizationContext(
            identity="project-agent",
            allowed_project_ids=frozenset({7}),
            allow_global_read=False,
        )

        with mcp_authorization_context(authorization):
            result = list_skills()
            self.assertEqual(len(result.skills), 1)
            self.assertEqual(result.skills[0].skill_id, "ros2-robotics")

    def test_rejects_skill_listing_without_authorization_context(self):
        with self.assertRaisesRegex(
            PermissionError,
            "authorization context is not available",
        ):
            list_skills()

    def test_skill_listing_does_not_retrieve_context_or_execute(self):
        with patch("app.mcp_server.assemble_canonical_context") as assemble:
            with patch("app.mcp_server.unified_search_context") as search:
                with patch(
                    "app.skills.ros2_robotics.ROS2RoboticsSkill.execute"
                ) as execute:
                    with mcp_authorization_context(self.authorization_context):
                        result = list_skills()

        self.assertEqual(result.skills[0].skill_id, "ros2-robotics")
        assemble.assert_not_called()
        search.assert_not_called()
        execute.assert_not_called()

    def test_registered_tool_serializes_skill_catalog(self):
        with mcp_authorization_context(self.authorization_context):
            result = asyncio.run(
                mcp.call_tool("list_skills", {})
            )

        self.assertFalse(result.is_error)
        self.assertEqual(
            result.structured_content["skills"][0]["skill_id"],
            "ros2-robotics",
        )
        self.assertEqual(
            set(result.structured_content["skills"][0]),
            {
                "skill_id",
                "name",
                "purpose",
                "domains",
                "task_types",
                "required_context",
                "input_schema",
                "output_schema",
                "evidence_type",
                "project_scope",
            },
        )

    def test_rejects_empty_query(self):
        with mcp_authorization_context(self.authorization_context):
            with self.assertRaisesRegex(ValueError, "must not be empty"):
                get_context("  ")

    def test_rejects_missing_authorization_before_core_call(self):
        with patch(
            "app.mcp_server.assemble_canonical_context",
        ) as assemble:
            with self.assertRaisesRegex(
                PermissionError,
                "authorization context is not available",
            ):
                get_context("publisher", project_id=7)

        assemble.assert_not_called()

    def test_forwards_project_and_clamped_limit_to_canonical_capability(self):
        canonical = CanonicalContext(
            query="publisher",
            project_id=7,
            memory_count=0,
            document_count=0,
            items=[],
            sources=[],
        )

        with mcp_authorization_context(self.authorization_context):
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

        with mcp_authorization_context(self.authorization_context):
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

        with mcp_authorization_context(self.authorization_context):
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
