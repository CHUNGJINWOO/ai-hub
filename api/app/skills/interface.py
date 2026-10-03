"""Minimal interfaces shared by External Agent skill prototypes."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from app.core.context_assembly import CanonicalContext, ContextSource
from app.core.authorization import AuthorizationContext


SkillStatus = Literal["success", "failure", "unknown"]


@dataclass(frozen=True)
class SkillRequest:
    """Input supplied by an External Agent to a skill."""

    question: str
    project_id: int
    limit: int = 5


@dataclass(frozen=True)
class SkillEvidence:
    """A factual claim linked to one canonical context source."""

    claim: str
    source_id: str


@dataclass(frozen=True)
class SkillResult:
    """Structured result that keeps evidence separate from reasoning."""

    status: SkillStatus
    result: str | None
    evidence: tuple[SkillEvidence, ...]
    inference: tuple[str, ...]
    unknown: tuple[str, ...]
    provenance: tuple[ContextSource, ...]
    validation: tuple[str, ...]

    @classmethod
    def failure(
        cls,
        message: str,
        *,
        validation: Sequence[str] = (),
    ) -> "SkillResult":
        return cls(
            status="failure",
            result=None,
            evidence=(),
            inference=(),
            unknown=(message,),
            provenance=(),
            validation=tuple(validation),
        )

    @classmethod
    def unknown_result(
        cls,
        message: str,
        *,
        provenance: Sequence[ContextSource] = (),
        validation: Sequence[str] = (),
    ) -> "SkillResult":
        return cls(
            status="unknown",
            result=None,
            evidence=(),
            inference=(),
            unknown=(message,),
            provenance=tuple(provenance),
            validation=tuple(validation),
        )


class ContextProvider(Protocol):
    """External adapter for the existing AI-Hub context capabilities."""

    def get_context(
        self,
        *,
        query: str,
        limit: int,
        project_id: int,
    ) -> CanonicalContext:
        """Return canonical context or raise a provider-specific failure."""

    def search_context(
        self,
        *,
        query: str,
        limit: int,
        project_id: int,
    ) -> CanonicalContext:
        """Return targeted canonical context or raise a provider failure."""


class Skill(Protocol):
    """Minimal contract implemented by an External Agent skill."""

    name: str
    purpose: str

    def execute(
        self,
        request: SkillRequest,
        *,
        context_provider: ContextProvider,
        authorization: AuthorizationContext,
    ) -> SkillResult:
        """Execute the skill against authorized AI-Hub context."""
