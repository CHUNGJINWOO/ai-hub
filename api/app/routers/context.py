from fastapi import APIRouter, HTTPException
from pgvector import Vector

from app.core.db import get_db_connection
from app.core.embedding import model


router = APIRouter(
    prefix="/context",
    tags=["context"],
)


# -------------------------
# Unified knowledge context search
# -------------------------

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

        limit = max(1, min(limit, 10))

        query_embedding = Vector(
            model.encode(
                "query: " + q,
                normalize_embeddings=True,
            ).tolist()
        )

        with get_db_connection() as conn:

            # ---------------------------------
            # Memory search
            # ---------------------------------

            if project_id is not None:
                memory_rows = conn.execute(
                    """
                    SELECT
                        id,
                        content,
                        memory_type,
                        category,
                        importance,
                        source,
                        project_id,
                        embedding <=> %s AS distance
                    FROM memories
                    WHERE embedding IS NOT NULL
                      AND project_id = %s
                    ORDER BY embedding <=> %s
                    LIMIT %s;
                    """,
                    (
                        query_embedding,
                        project_id,
                        query_embedding,
                        limit,
                    ),
                ).fetchall()

            else:
                memory_rows = conn.execute(
                    """
                    SELECT
                        id,
                        content,
                        memory_type,
                        category,
                        importance,
                        source,
                        project_id,
                        embedding <=> %s AS distance
                    FROM memories
                    WHERE embedding IS NOT NULL
                    ORDER BY embedding <=> %s
                    LIMIT %s;
                    """,
                    (
                        query_embedding,
                        query_embedding,
                        limit,
                    ),
                ).fetchall()

            # ---------------------------------
            # Document chunk search
            # ---------------------------------

            if project_id is not None:
                document_rows = conn.execute(
                    """
                    SELECT
                        c.id,
                        c.document_id,
                        d.project_id,
                        d.title,
                        d.filename,
                        c.chunk_index,
                        c.page_number,
                        c.content,
                        c.embedding <=> %s AS distance
                    FROM document_chunks c
                    JOIN documents d
                        ON d.id = c.document_id
                    WHERE c.embedding IS NOT NULL
                      AND d.project_id = %s
                    ORDER BY c.embedding <=> %s
                    LIMIT %s;
                    """,
                    (
                        query_embedding,
                        project_id,
                        query_embedding,
                        limit,
                    ),
                ).fetchall()

            else:
                document_rows = conn.execute(
                    """
                    SELECT
                        c.id,
                        c.document_id,
                        d.project_id,
                        d.title,
                        d.filename,
                        c.chunk_index,
                        c.page_number,
                        c.content,
                        c.embedding <=> %s AS distance
                    FROM document_chunks c
                    JOIN documents d
                        ON d.id = c.document_id
                    WHERE c.embedding IS NOT NULL
                    ORDER BY c.embedding <=> %s
                    LIMIT %s;
                    """,
                    (
                        query_embedding,
                        query_embedding,
                        limit,
                    ),
                ).fetchall()

        # ---------------------------------
        # Response formatting
        # ---------------------------------

        memories = [
            {
                "id": row[0],
                "content": row[1],
                "memory_type": row[2],
                "category": row[3],
                "importance": row[4],
                "source": row[5],
                "project_id": row[6],
                "distance": row[7],
            }
            for row in memory_rows
        ]

        documents = [
            {
                "chunk_id": row[0],
                "document_id": row[1],
                "project_id": row[2],
                "title": row[3],
                "filename": row[4],
                "chunk_index": row[5],
                "page_number": row[6],
                "content": row[7],
                "distance": row[8],
            }
            for row in document_rows
        ]

        return {
            "query": q,
            "project_id": project_id,
            "memory_count": len(memories),
            "document_count": len(documents),
            "memories": memories,
            "documents": documents,
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))