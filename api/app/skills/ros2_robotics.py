"""Reference ROS2/Robotics skill using AI-Hub canonical context."""

from dataclasses import dataclass

from app.core.authorization import (
    AuthorizationContext,
    AuthorizationDenied,
    require_project_access,
)
from app.core.context_assembly import CanonicalContext
from app.skills.interface import (
    ContextProvider,
    Skill,
    SkillEvidence,
    SkillRequest,
    SkillResult,
)


class ContextRetrievalError(RuntimeError):
    """Raised by a context adapter when AI-Hub retrieval cannot complete."""


@dataclass(frozen=True)
class ROS2RoboticsSkill(Skill):
    """Small reference implementation for project-scoped ROS2 analysis."""

    name: str = "ros2-robotics"
    purpose: str = "Analyze ROS2 and robotics questions using project context."

    def execute(
        self,
        request: SkillRequest,
        *,
        context_provider: ContextProvider,
        authorization: AuthorizationContext,
    ) -> SkillResult:
        if not request.question.strip():
            return SkillResult.failure(
                "question must not be empty",
                validation=("provide a ROS2 or robotics question",),
            )

        if not self._is_ros2_question(request.question):
            return SkillResult.failure(
                "question is outside the ROS2/Robotics skill scope",
                validation=("select a skill matching the requested domain",),
            )

        try:
            require_project_access(
                authorization,
                operation="read",
                project_id=request.project_id,
            )
        except AuthorizationDenied as exc:
            return SkillResult.failure(
                str(exc),
                validation=("authorization must be established before retrieval",),
            )

        try:
            context = context_provider.get_context(
                query=request.question,
                limit=max(1, min(request.limit, 20)),
                project_id=request.project_id,
            )
        except ContextRetrievalError as exc:
            return SkillResult.failure(
                f"AI-Hub context retrieval failed: {exc}",
                validation=("retry only after the context provider is available",),
            )

        provenance_error = self._validate_context(context, request.project_id)
        if provenance_error is not None:
            return SkillResult.failure(
                provenance_error,
                validation=("retrieve canonical context with complete provenance",),
            )

        if not context.items:
            return SkillResult.unknown_result(
                "required ROS2 context was not found",
                provenance=context.sources,
                validation=("perform targeted retrieval or provide more context",),
            )

        evidence = tuple(
            SkillEvidence(claim=item.content, source_id=item.source_id)
            for item in context.items
        )
        inference = (
            f"Retrieved {len(evidence)} project-scoped evidence item(s) "
            f"for the ROS2/Robotics question.",
        )
        return SkillResult(
            status="success",
            result=(
                "ROS2/Robotics analysis is grounded in the retrieved "
                "project-scoped evidence."
            ),
            evidence=evidence,
            inference=inference,
            unknown=(),
            provenance=tuple(context.sources),
            validation=(
                "canonical context source references were validated",
                "evidence and inference remain separate",
            ),
        )

    @staticmethod
    def _is_ros2_question(question: str) -> bool:
        normalized = question.casefold()
        return any(
            term in normalized
            for term in ("ros2", "ros 2", "robotics", "robot", "nav2")
        )

    @staticmethod
    def _validate_context(
        context: CanonicalContext,
        project_id: int,
    ) -> str | None:
        if context.project_id != project_id:
            return "retrieved context project scope does not match the request"

        source_ids = {source.source_id for source in context.sources}
        for source in context.sources:
            if source.project_id != project_id:
                return "retrieved context provenance is incomplete or out of scope"
        if any(item.source_id not in source_ids for item in context.items):
            return "retrieved context contains an item without provenance"
        return None
