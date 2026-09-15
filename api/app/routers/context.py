from fastapi import APIRouter, HTTPException

from app.core.search import search_context as unified_search_context


router = APIRouter(
    prefix="/context",
    tags=["context"],
)


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