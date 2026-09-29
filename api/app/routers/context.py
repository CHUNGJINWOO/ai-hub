from fastapi import APIRouter, HTTPException

from app.core.context_assembly import assemble_context as build_context
from app.core.search import search_context as unified_search_context


router = APIRouter(
    prefix="/context",
    tags=["context"],
)


@router.get("/assemble")
def assemble_context(
    q: str,
    limit: int = 5,
    project_id: int | None = None,
):
    """Return normalized context items with citation-ready source records."""
    try:
        if not q.strip():
            raise HTTPException(
                status_code=400,
                detail="Query must not be empty",
            )

        return build_context(query=q, limit=limit, project_id=project_id)

    except HTTPException:
        raise

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search")
def search_context(
    q: str,
    limit: int = 5,
    project_id: int | None = None,
):
    try:
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
