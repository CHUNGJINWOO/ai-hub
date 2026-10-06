from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Literal
from collections.abc import Generator

from fastapi import Request

from app.core.capabilities import Capability
from app.core.scopes import GlobalScope, ProjectScope, ResourceScope


AuthorizationOperation = Literal["read", "write"]


class AuthorizationDenied(PermissionError):
    """Raised when a request is outside its authorized scope or capability."""

    def __init__(
        self,
        message: str = "Authorization denied",
        *,
        scope: Any = None,
        capability: Any = None,
    ) -> None:
        super().__init__(message)
        self.scope = scope
        self.capability = capability


@dataclass(frozen=True, init=False)
class AuthorizationContext:
    """Immutable principal authorization context evaluated per request."""

    identity: str
    read_project_ids: frozenset[int] = frozenset()
    write_project_ids: frozenset[int] = frozenset()
    capabilities: frozenset[Capability] = frozenset()

    def __init__(
        self,
        identity: str,
        read_project_ids: frozenset[int] | None = None,
        write_project_ids: frozenset[int] | None = None,
        capabilities: frozenset[Capability] | None = None,
        *,
        # Compatibility arguments for legacy callers (Steps 1~3)
        allowed_project_ids: frozenset[int] | None = None,
        allow_global_read: bool | None = None,
        allow_write: bool | None = None,
    ) -> None:
        if not identity:
            raise ValueError("identity must not be empty")

        # Resolve read_project_ids
        if read_project_ids is not None:
            resolved_read = frozenset(read_project_ids)
        elif allowed_project_ids is not None:
            resolved_read = frozenset(allowed_project_ids)
        else:
            resolved_read = frozenset()

        # Resolve write_project_ids
        if write_project_ids is not None:
            resolved_write = frozenset(write_project_ids)
        elif allow_write is True:
            # Legacy allow_write maps write_project_ids to read_project_ids
            resolved_write = resolved_read
        else:
            resolved_write = frozenset()

        # Resolve capabilities
        caps: set[Capability] = set(capabilities) if capabilities else set()
        if allow_global_read is True:
            caps.add(Capability.RESOURCE_READ_GLOBAL)
        resolved_capabilities = frozenset(caps)

        # Enforce subset invariant: write_project_ids ⊆ read_project_ids
        if not resolved_write.issubset(resolved_read):
            raise ValueError("write_project_ids must be a subset of read_project_ids")

        object.__setattr__(self, "identity", str(identity))
        object.__setattr__(self, "read_project_ids", resolved_read)
        object.__setattr__(self, "write_project_ids", resolved_write)
        object.__setattr__(self, "capabilities", resolved_capabilities)
        if allow_write is not None:
            object.__setattr__(self, "_legacy_allow_write", bool(allow_write))

    # -----------------------------------------------------------------------
    # Compatibility properties for legacy callers (routers / Step 1~3 code)
    # -----------------------------------------------------------------------

    @property
    def allowed_project_ids(self) -> frozenset[int]:
        """Deprecated compatibility property returning read_project_ids."""
        return self.read_project_ids

    @property
    def allow_global_read(self) -> bool:
        """Deprecated compatibility property checking RESOURCE_READ_GLOBAL."""
        return Capability.RESOURCE_READ_GLOBAL in self.capabilities

    @property
    def allow_write(self) -> bool:
        """Deprecated compatibility property checking write permissions."""
        if getattr(self, "_legacy_allow_write", None) is not None:
            return bool(self._legacy_allow_write)
        return (
            bool(self.write_project_ids)
            or Capability.RESOURCE_WRITE_GLOBAL in self.capabilities
        )

    # -----------------------------------------------------------------------
    # Pure predicate methods
    # -----------------------------------------------------------------------

    def can_read_project(self, project_id: int) -> bool:
        return (
            Capability.PROJECT_READ_ALL in self.capabilities
            or project_id in self.read_project_ids
        )

    def can_write_project(self, project_id: int) -> bool:
        return project_id in self.write_project_ids

    def can_read_global(self) -> bool:
        return Capability.RESOURCE_READ_GLOBAL in self.capabilities

    def can_write_global(self) -> bool:
        return Capability.RESOURCE_WRITE_GLOBAL in self.capabilities

    def can_create_project(self) -> bool:
        return Capability.PROJECT_CREATE in self.capabilities

    def can_admin_project(self, project_id: int) -> bool:
        return Capability.PROJECT_ADMIN in self.capabilities


_mcp_authorization_context: ContextVar[AuthorizationContext | None] = (
    ContextVar(
        "mcp_authorization_context",
        default=None,
    )
)


# ---------------------------------------------------------------------------
# Pure Authorization Engine Functions (v2)
# ---------------------------------------------------------------------------

def authorize_read(
    context: AuthorizationContext,
    scope: ResourceScope,
) -> None:
    """Authorize read access against a ProjectScope or GlobalScope."""
    if not isinstance(context, AuthorizationContext):
        raise TypeError(
            f"context must be an AuthorizationContext instance, got {type(context).__name__}"
        )
    if scope is None:
        raise ValueError("scope must not be None")
    if not isinstance(scope, (ProjectScope, GlobalScope)):
        raise TypeError(
            f"scope must be a ResourceScope (ProjectScope or GlobalScope), got {type(scope).__name__}"
        )

    if isinstance(scope, ProjectScope):
        if context.can_read_project(scope.project_id):
            return
        raise AuthorizationDenied(
            f"identity is not authorized to read project {scope.project_id}: not authorized for project: {scope.project_id}",
            scope=scope,
        )

    if isinstance(scope, GlobalScope):
        if context.can_read_global():
            return
        raise AuthorizationDenied(
            "identity is not authorized for global resource read",
            scope=scope,
            capability=Capability.RESOURCE_READ_GLOBAL,
        )


def authorize_write(
    context: AuthorizationContext,
    scope: ResourceScope,
) -> None:
    """Authorize write access against a ProjectScope or GlobalScope."""
    if not isinstance(context, AuthorizationContext):
        raise TypeError(
            f"context must be an AuthorizationContext instance, got {type(context).__name__}"
        )
    if scope is None:
        raise ValueError("scope must not be None")
    if not isinstance(scope, (ProjectScope, GlobalScope)):
        raise TypeError(
            f"scope must be a ResourceScope (ProjectScope or GlobalScope), got {type(scope).__name__}"
        )

    if isinstance(scope, ProjectScope):
        if context.can_write_project(scope.project_id):
            return
        raise AuthorizationDenied(
            f"identity is not authorized to write project {scope.project_id}: not authorized for project: {scope.project_id}",
            scope=scope,
        )

    if isinstance(scope, GlobalScope):
        if context.can_write_global():
            return
        raise AuthorizationDenied(
            "identity is not authorized for global resource write",
            scope=scope,
            capability=Capability.RESOURCE_WRITE_GLOBAL,
        )


def authorize_project_create(
    context: AuthorizationContext,
) -> None:
    """Authorize project creation."""
    if not isinstance(context, AuthorizationContext):
        raise TypeError(
            f"context must be an AuthorizationContext instance, got {type(context).__name__}"
        )

    if context.can_create_project():
        return

    raise AuthorizationDenied(
        "identity is not authorized to create projects",
        capability=Capability.PROJECT_CREATE,
    )


def authorize_project_admin(
    context: AuthorizationContext,
    project_id: int,
) -> None:
    """Authorize administrative management of a specific project."""
    if not isinstance(context, AuthorizationContext):
        raise TypeError(
            f"context must be an AuthorizationContext instance, got {type(context).__name__}"
        )
    if project_id is None:
        raise ValueError("project_id must not be None")
    if not isinstance(project_id, int):
        raise TypeError(
            f"project_id must be an integer, got {type(project_id).__name__}"
        )

    if context.can_admin_project(project_id):
        return

    raise AuthorizationDenied(
        f"identity is not authorized to administer project {project_id}",
        capability=Capability.PROJECT_ADMIN,
    )


# ---------------------------------------------------------------------------
# Claim Adapter (v2 Standard Claims & Legacy Compatibility)
# ---------------------------------------------------------------------------

def claims_to_authorization_context(
    identity: str | None,
    claims: dict[str, Any],
) -> AuthorizationContext:
    """Translate a verified principal identity and claims into an AuthorizationContext v2."""
    if not identity:
        raise AuthorizationDenied(
            "authenticated token does not contain a principal"
        )

    # 1. Resolve read_project_ids
    raw_read = claims.get("read_project_ids")
    raw_legacy_projects = claims.get("allowed_project_ids")

    if raw_read is not None:
        if not isinstance(raw_read, (list, tuple, set, frozenset)):
            raise AuthorizationDenied(
                "authenticated token contains invalid project permissions"
            )
        try:
            read_project_ids = frozenset(int(pid) for pid in raw_read)
        except (TypeError, ValueError) as exc:
            raise AuthorizationDenied(
                "authenticated token contains invalid project permissions"
            ) from exc
    elif raw_legacy_projects is not None:
        if not isinstance(raw_legacy_projects, (list, tuple, set, frozenset)):
            raise AuthorizationDenied(
                "authenticated token contains invalid project permissions"
            )
        try:
            read_project_ids = frozenset(
                int(pid) for pid in raw_legacy_projects
            )
        except (TypeError, ValueError) as exc:
            raise AuthorizationDenied(
                "authenticated token contains invalid project permissions"
            ) from exc
    else:
        read_project_ids = frozenset()

    # 2. Resolve capabilities
    caps: set[Capability] = set()
    raw_caps = claims.get("capabilities")
    if raw_caps is not None:
        if not isinstance(raw_caps, (list, tuple, set, frozenset)):
            raise AuthorizationDenied(
                "authenticated token contains invalid capabilities"
            )
        for item in raw_caps:
            try:
                cap_val = (
                    Capability(item)
                    if isinstance(item, str)
                    else Capability(item.value)
                )
                caps.add(cap_val)
            except (ValueError, KeyError, AttributeError) as exc:
                raise AuthorizationDenied(
                    f"authenticated token contains unknown capability: {item}"
                ) from exc

    # Legacy capability mappings
    auth_method = claims.get("auth_method")
    if (
        auth_method == "static_api_key"
        or claims.get("allow_global_read") is True
    ):
        caps.add(Capability.RESOURCE_READ_GLOBAL)

    # 3. Resolve write_project_ids
    raw_write = claims.get("write_project_ids")
    if raw_write is not None:
        if not isinstance(raw_write, (list, tuple, set, frozenset)):
            raise AuthorizationDenied(
                "authenticated token contains invalid write project permissions"
            )
        try:
            write_project_ids = frozenset(int(pid) for pid in raw_write)
        except (TypeError, ValueError) as exc:
            raise AuthorizationDenied(
                "authenticated token contains invalid write project permissions"
            ) from exc
    elif claims.get("allow_write") is True:
        # Legacy allow_write maps write_project_ids to read_project_ids.
        # It does NOT grant PROJECT_CREATE or PROJECT_ADMIN.
        write_project_ids = read_project_ids
    else:
        write_project_ids = frozenset()

    # 4. Enforce subset invariant
    if not write_project_ids.issubset(read_project_ids):
        raise AuthorizationDenied(
            "write_project_ids must be a subset of read_project_ids"
        )

    legacy_allow_write = claims.get("allow_write")
    return AuthorizationContext(
        identity=str(identity),
        read_project_ids=read_project_ids,
        write_project_ids=write_project_ids,
        capabilities=frozenset(caps),
        allow_write=legacy_allow_write if isinstance(legacy_allow_write, bool) else None,
    )


# Alias for compatibility
authorization_context_from_claims = claims_to_authorization_context


# ---------------------------------------------------------------------------
# Legacy Functions (preserved for Steps 1~3 router and MCP server callers)
# ---------------------------------------------------------------------------

def get_request_authorization_context(
    request: Request | None,
) -> AuthorizationContext:
    """Extract and validate the AuthorizationContext bound to the current request."""
    context = (
        getattr(request.state, "authorization_context", None)
        if request is not None
        else None
    )
    if not isinstance(context, AuthorizationContext):
        raise AuthorizationDenied(
            "authorization context is not available for this request"
        )
    return context


def require_request_project_access(
    request: Request | None,
    *,
    operation: AuthorizationOperation,
    project_id: int | None,
) -> None:
    context = get_request_authorization_context(request)
    require_project_access(
        context,
        operation=operation,
        project_id=project_id,
    )


def require_request_project_read_access(
    request: Request | None,
    project_id: int,
) -> None:
    """Permit read access if global read is allowed or project_id is in allowed_project_ids."""
    context = get_request_authorization_context(request)
    if context.allow_global_read or project_id in context.allowed_project_ids:
        return
    raise AuthorizationDenied(
        f"identity is not authorized for project: {project_id}"
    )


def require_request_project_write_access(
    request: Request | None,
    project_id: int | None = None,
) -> None:
    """Enforce durable write permissions for project creation or mutation."""
    context = get_request_authorization_context(request)
    if not context.allow_write:
        raise AuthorizationDenied(
            f"identity is not authorized for durable writes: {context.identity}"
        )
    if project_id is not None and project_id not in context.write_project_ids:
        raise AuthorizationDenied(
            f"identity is not authorized for project: {project_id}"
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


def get_mcp_authorization_context() -> AuthorizationContext:
    """Extract and validate the AuthorizationContext bound to the current MCP request."""
    context = _mcp_authorization_context.get()
    if context is None:
        raise AuthorizationDenied(
            "authorization context is not available for this request"
        )
    return context


def require_mcp_project_access(
    *,
    operation: AuthorizationOperation,
    project_id: int | None,
) -> None:
    context = get_mcp_authorization_context()

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
    if project_id is None:
        if operation == "read" and context.allow_global_read:
            return
        raise AuthorizationDenied(
            "project_id is required for this authorization request"
        )

    if operation == "write" and not context.allow_write:
        raise AuthorizationDenied(
            f"identity is not authorized for durable writes: {context.identity}"
        )

    if operation == "read":
        if (
            project_id not in context.allowed_project_ids
            and not (Capability.PROJECT_READ_ALL in context.capabilities)
        ):
            raise AuthorizationDenied(
                f"identity is not authorized for project: {project_id}"
            )
    elif operation == "write":
        if project_id not in context.write_project_ids:
            raise AuthorizationDenied(
                f"identity is not authorized for project: {project_id}"
            )
