import unittest

from app.skills import SkillDescriptor, list_skill_descriptors
from app.skills.ros2_robotics import ROS2RoboticsSkill


class SkillDiscoveryTests(unittest.TestCase):
    def test_discovers_ros2_robotics_descriptor(self):
        descriptors = list_skill_descriptors()

        self.assertIn("ros2-robotics", {item.skill_id for item in descriptors})

    def test_returns_skill_descriptors(self):
        descriptors = list_skill_descriptors()

        self.assertIsInstance(descriptors, tuple)
        self.assertTrue(descriptors)
        self.assertTrue(
            all(isinstance(item, SkillDescriptor) for item in descriptors)
        )

    def test_ros2_descriptor_has_stable_metadata(self):
        descriptor = list_skill_descriptors()[0]

        self.assertEqual(descriptor.skill_id, "ros2-robotics")
        self.assertEqual(descriptor.domains, ("ros2", "robotics"))
        self.assertEqual(descriptor.project_scope, "project-scoped")

    def test_ros2_descriptor_matches_concrete_skill_contract(self):
        descriptor = list_skill_descriptors()[0]
        skill = ROS2RoboticsSkill()

        self.assertEqual(descriptor.skill_id, skill.skill_id)
        self.assertEqual(descriptor.input_schema, "SkillRequest")
        self.assertEqual(descriptor.output_schema, "SkillResult")
        self.assertEqual(
            descriptor.required_context,
            (
                "project-scoped canonical context",
                "source provenance",
                "project_id",
            ),
        )
        self.assertEqual(descriptor.evidence_type, "ContextItem + ContextSource")
        self.assertEqual(descriptor.project_scope, "project-scoped")

    def test_discovery_is_deterministic(self):
        self.assertEqual(list_skill_descriptors(), list_skill_descriptors())

    def test_discovery_does_not_execute_a_skill(self):
        descriptors = list_skill_descriptors()

        self.assertEqual(len(descriptors), 1)
        self.assertEqual(descriptors[0].output_schema, "SkillResult")

    def test_discovered_skill_ids_are_unique(self):
        descriptors = list_skill_descriptors()

        skill_ids = [descriptor.skill_id for descriptor in descriptors]
        self.assertEqual(len(skill_ids), len(set(skill_ids)))


if __name__ == "__main__":
    unittest.main()
