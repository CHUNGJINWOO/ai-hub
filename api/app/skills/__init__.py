"""Minimal contracts for External Agent skills."""

from app.skills.discovery import list_skill_descriptors
from app.skills.interface import (
    ContextProvider,
    Skill,
    SkillDescriptor,
    SkillRequest,
    SkillEvidence,
    SkillResult,
)

__all__ = [
    "ContextProvider",
    "list_skill_descriptors",
    "Skill",
    "SkillDescriptor",
    "SkillRequest",
    "SkillEvidence",
    "SkillResult",
]
