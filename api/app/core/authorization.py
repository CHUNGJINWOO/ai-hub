from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal
from collections.abc import Generator

from fastapi import Request


AuthorizationOperation = Literal["read", "write"]


class AuthorizationDenied(PermissionError):
    """Raised when a request is outside its authorized project scope."""


@dataclass(frozen=True)
class AuthorizationContext:
    """Authorization result supplied by an external authentication boundary."""

    identity: str
    allowed_project_ids: frozenset[int]
    allow_global_read: bool = False
    allow_write: bool = False


_mcp_authorization_context: ContextVar[
    AuthorizationContext | None
] = ContextVar(
    "mcp_authorization_context",
    default=None,
)


def require_request_project_access(
    request: Request | None,
    *,
    operation: AuthorizationOperation,
    project_id: int | None,
) -> None:
    context = (
        getattr(request.state, "authorization_context", None)
        if request is not None
        else None
    )
    if not isinstance(context, AuthorizationContext):
        raise AuthorizationDenied(
            "authorization context is not available for this request"
        )

    require_project_access(
        context,
        operation=operation,
        project_id=project_id,
    )


def require_bound_request_project_access(
    request: Request | None,
    *,
    operation: AuthorizationOperation,
    project_id: int | None,
) -> None:
    """Guard an HTTP request while preserving direct unit-call compatibility."""
    if request is not None:
        require_request_project_access(
            request,
            operation=operation,
            project_id=project_id,
        )


def require_mcp_project_access(
    *,
    operation: AuthorizationOperation,
    project_id: int | None,
) -> None:
    context = _mcp_authorization_context.get()
    if context is None:
        raise AuthorizationDenied(
            "authorization context is not available for this request"
        )

    require_project_access(
        context,
        operation=operation,
        project_id=project_id,
    )


@contextmanager
def mcp_authorization_context(
    context: AuthorizationContext,
) -> Generator[None, None, None]:
    token = _mcp_authorization_context.set(context)
    try:
        yield
    finally:
        _mcp_authorization_context.reset(token)


def require_project_access(
    context: AuthorizationContext,
    *,
    operation: AuthorizationOperation,
    project_id: int | None,
) -> None:
    """Enforce project authorization before data filtering or durable writes."""
    if operation == "write" and not context.allow_write:
        raise AuthorizationDenied(
            f"identity is not authorized for durable writes: {context.identity}"
        )

    if project_id is None:
        if operation == "read" and context.allow_global_read:
            return
        if operation == "write" and context.allow_write:
            return
        raise AuthorizationDenied(
            "project_id is required for this authorization request"
        )

    if project_id not in context.allowed_project_ids:
        raise AuthorizationDenied(
            f"identity is not authorized for project: {project_id}"
        )
