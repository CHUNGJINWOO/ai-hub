import unittest
from dataclasses import FrozenInstanceError

from app.skills import SkillDescriptor


def valid_descriptor() -> SkillDescriptor:
    return SkillDescriptor(
        skill_id="example-skill",
        name="Example Skill",
        purpose="Demonstrate a capability descriptor.",
        domains=("example",),
        task_types=("analysis",),
        required_context=("project-scoped canonical context",),
        input_schema="SkillRequest",
        output_schema="SkillResult",
        evidence_type="ContextItem + ContextSource",
        project_scope="project-scoped",
    )


class SkillDescriptorTests(unittest.TestCase):
    def test_creates_descriptor_with_required_metadata(self):
        descriptor = valid_descriptor()

        self.assertEqual(descriptor.skill_id, "example-skill")
        self.assertEqual(descriptor.domains, ("example",))
        self.assertEqual(descriptor.output_schema, "SkillResult")

    def test_descriptor_is_immutable(self):
        descriptor = valid_descriptor()

        with self.assertRaises(FrozenInstanceError):
            descriptor.name = "Changed"

    def test_missing_required_metadata_fails_at_construction(self):
        with self.assertRaises(TypeError):
            SkillDescriptor(  # type: ignore[call-arg]
                skill_id="incomplete",
                name="Incomplete",
            )


if __name__ == "__main__":
    unittest.main()
