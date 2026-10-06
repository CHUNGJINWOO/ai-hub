from fastapi import APIRouter, HTTPException, Request

from app.core.authorization import (
    AuthorizationDenied,
    authorize_read,
    get_request_authorization_context,
)
from app.core.capabilities import Capability
from app.core.scopes import ProjectScope
from app.core.context_assembly import assemble_context as build_context
from app.core.search import search_context as unified_search_context


router = APIRouter(
    prefix="/context",
    tags=["context"],
)


@router.get("/assemble")
def assemble_context(
    request: Request,
    q: str,
    limit: int = 5,
    project_id: int | None = None,
):
    """Return normalized context items with citation-ready source records."""
    try:
        context = get_request_authorization_context(request)
        if project_id is not None:
            authorize_read(context, ProjectScope(project_id))
        else:
            if not (context.can_read_global() or Capability.PROJECT_READ_ALL in context.capabilities):
                raise AuthorizationDenied(
                    "project_id is required for this authorization request"
                )

        if not q.strip():
            raise HTTPException(
                status_code=400,
                detail="Query must not be empty",
            )

        return build_context(query=q, limit=limit, project_id=project_id)

    except HTTPException:
        raise

    except AuthorizationDenied as e:
        raise HTTPException(status_code=403, detail=str(e))

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search")
def search_context(
    request: Request,
    q: str,
    limit: int = 5,
    project_id: int | None = None,
):
    try:
        context = get_request_authorization_context(request)
        if project_id is not None:
            authorize_read(context, ProjectScope(project_id))
        else:
            if not (context.can_read_global() or Capability.PROJECT_READ_ALL in context.capabilities):
                raise AuthorizationDenied(
                    "project_id is required for this authorization request"
                )

        if not q.strip():
            raise HTTPException(
                status_code=400,
                detail="Query must not be empty",
            )

        return unified_search_context(
            query=q,
            limit=limit,
            project_id=project_id,
        )

    except HTTPException:
        raise

    except AuthorizationDenied as e:
        raise HTTPException(status_code=403, detail=str(e))

    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e),
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )
