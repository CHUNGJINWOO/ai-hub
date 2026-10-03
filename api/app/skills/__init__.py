"""Minimal contracts for External Agent skills."""

from app.skills.interface import (
    ContextProvider,
    Skill,
    SkillRequest,
    SkillEvidence,
    SkillResult,
)

__all__ = [
    "ContextProvider",
    "Skill",
    "SkillRequest",
    "SkillEvidence",
    "SkillResult",
]
