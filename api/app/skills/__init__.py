"""Minimal contracts for External Agent skills."""

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
    "Skill",
    "SkillDescriptor",
    "SkillRequest",
    "SkillEvidence",
    "SkillResult",
]
