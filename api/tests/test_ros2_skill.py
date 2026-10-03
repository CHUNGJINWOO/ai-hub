import unittest
from unittest.mock import Mock

from app.core.authorization import AuthorizationContext
from app.core.context_assembly import CanonicalContext
from app.skills.ros2_robotics import ContextRetrievalError, ROS2RoboticsSkill
from app.skills.interface import SkillRequest


def canonical_context(
    *,
    project_id: int = 7,
    content: str = "The node publishes cmd_vel.",
) -> CanonicalContext:
    return CanonicalContext(
        query="Explain ROS2 cmd_vel publishing",
        project_id=project_id,
        memory_count=0,
        document_count=1,
        items=[
            {
                "item_id": "document-chunk:3",
                "kind": "document",
                "content": content,
                "source_id": "document-chunk:3",
                "metadata": {"filename": "driver.cpp"},
            }
        ],
        sources=[
            {
                "source_id": "document-chunk:3",
                "kind": "document",
                "project_id": project_id,
                "document_id": 2,
                "chunk_id": 3,
            }
        ],
    )


class ROS2RoboticsSkillTests(unittest.TestCase):
    def setUp(self):
        self.skill = ROS2RoboticsSkill()
        self.authorization = AuthorizationContext(
            identity="agent-1",
            allowed_project_ids=frozenset({7}),
        )
        self.provider = Mock()
        self.provider.get_context.return_value = canonical_context()
        self.request = SkillRequest(
            question="Explain the ROS2 cmd_vel publisher",
            project_id=7,
        )

    def test_exposes_minimal_skill_interface_and_structured_result(self):
        result = self.skill.execute(
            self.request,
            context_provider=self.provider,
            authorization=self.authorization,
        )

        self.assertEqual(self.skill.name, "ros2-robotics")
        self.assertTrue(self.skill.purpose)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.evidence[0].source_id, "document-chunk:3")
        self.assertEqual(result.inference[0][:9], "Retrieved")
        self.assertEqual(result.unknown, ())
        self.assertEqual(result.provenance[0].source_id, "document-chunk:3")
        self.provider.get_context.assert_called_once_with(
            query=self.request.question,
            limit=5,
            project_id=7,
        )

    def test_denies_unauthorized_project_before_retrieval(self):
        request = SkillRequest(
            question="Explain the ROS2 cmd_vel publisher",
            project_id=8,
        )

        result = self.skill.execute(
            request,
            context_provider=self.provider,
            authorization=self.authorization,
        )

        self.assertEqual(result.status, "failure")
        self.assertIn("not authorized", result.unknown[0])
        self.provider.get_context.assert_not_called()

    def test_returns_unknown_for_empty_context(self):
        self.provider.get_context.return_value = CanonicalContext(
            query=self.request.question,
            project_id=7,
            memory_count=0,
            document_count=0,
            items=[],
            sources=[],
        )

        result = self.skill.execute(
            self.request,
            context_provider=self.provider,
            authorization=self.authorization,
        )

        self.assertEqual(result.status, "unknown")
        self.assertIn("context was not found", result.unknown[0])
        self.assertIsNone(result.result)

    def test_rejects_incomplete_provenance(self):
        context = canonical_context()
        context.sources[0].project_id = None
        self.provider.get_context.return_value = context

        result = self.skill.execute(
            self.request,
            context_provider=self.provider,
            authorization=self.authorization,
        )

        self.assertEqual(result.status, "failure")
        self.assertIn("provenance", result.unknown[0])

    def test_returns_failure_for_retrieval_error(self):
        self.provider.get_context.side_effect = ContextRetrievalError(
            "provider unavailable"
        )

        result = self.skill.execute(
            self.request,
            context_provider=self.provider,
            authorization=self.authorization,
        )

        self.assertEqual(result.status, "failure")
        self.assertIn("provider unavailable", result.unknown[0])

    def test_rejects_out_of_scope_question(self):
        request = SkillRequest(
            question="Explain a database indexing strategy",
            project_id=7,
        )

        result = self.skill.execute(
            request,
            context_provider=self.provider,
            authorization=self.authorization,
        )

        self.assertEqual(result.status, "failure")
        self.provider.get_context.assert_not_called()


if __name__ == "__main__":
    unittest.main()
