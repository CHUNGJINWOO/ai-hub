"""Static discovery of External Agent skill capability metadata."""

from app.skills.interface import SkillDescriptor


_STATIC_SKILL_DESCRIPTORS = (
    SkillDescriptor(
        skill_id="ros2-robotics",
        name="ROS2 Robotics",
        purpose=(
            "Analyze ROS2 and robotics questions using project-scoped "
            "context."
        ),
        domains=("ros2", "robotics"),
        task_types=(
            "node",
            "topic",
            "service",
            "action",
            "parameter",
            "launch",
            "nav2",
        ),
        required_context=(
            "project-scoped canonical context",
            "source provenance",
            "project_id",
        ),
        input_schema="SkillRequest",
        output_schema="SkillResult",
        evidence_type="ContextItem + ContextSource",
        project_scope="project-scoped",
    ),
)


def list_skill_descriptors() -> tuple[SkillDescriptor, ...]:
    """Return the deterministic, read-only static skill catalog."""
    skill_ids = [
        descriptor.skill_id for descriptor in _STATIC_SKILL_DESCRIPTORS
    ]
    if len(skill_ids) != len(set(skill_ids)):
        raise ValueError(
            "static skill descriptors must have unique skill_id values"
        )

    return _STATIC_SKILL_DESCRIPTORS
