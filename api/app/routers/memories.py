from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from pgvector import Vector

from app.core.db import get_db_connection
from app.core.embedding import model


router = APIRouter(
    prefix="/memories",
    tags=["memories"],
)


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1)
    memory_type: str = "fact"
    category: str | None = None
    importance: int = Field(default=3, ge=1, le=5)
    source: str | None = "api"
    project_id: int | None = None


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1)
    memory_type: str | None = None
    category: str | None = None
    importance: int | None = Field(default=None, ge=1, le=5)
    source: str | None = None
    project_id: int | None = None



# -------------------------
# Create memory
# -------------------------

@router.post("")
def create_memory(memory: MemoryCreate):
    try:
        embedding = Vector(
            model.encode(
                "passage: " + memory.content,
                normalize_embeddings=True,
            ).tolist()
        )

        with get_db_connection() as conn:
            if memory.project_id is not None:
                project = conn.execute(
                    """
                    SELECT id
                    FROM projects
                    WHERE id = %s;
                    """,
                    (memory.project_id,),
                ).fetchone()

                if project is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Project not found",
                    )

            result = conn.execute(
                """
                INSERT INTO memories (
                    content,
                    memory_type,
                    category,
                    importance,
                    source,
                    embedding,
                    project_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (
                    memory.content,
                    memory.memory_type,
                    memory.category,
                    memory.importance,
                    memory.source,
                    embedding,
                    memory.project_id,
                ),
            )

            memory_id = result.fetchone()[0]
            conn.commit()

        return {
            "status": "created",
            "id": memory_id,
            "content": memory.content,
            "project_id": memory.project_id,
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------
# List memories
# -------------------------

@router.get("")
def list_memories(
    limit: int = 50,
    offset: int = 0,
    project_id: int | None = None,
):
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    try:
        with get_db_connection() as conn:
            if project_id is not None:
                rows = conn.execute(
                    """
                    SELECT
                        id,
                        content,
                        memory_type,
                        category,
                        importance,
                        source,
                        project_id,
                        created_at,
                        updated_at,
                        embedding IS NOT NULL AS has_embedding
                    FROM memories
                    WHERE project_id = %s
                    ORDER BY id
                    LIMIT %s OFFSET %s;
                    """,
                    (project_id, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT
                        id,
                        content,
                        memory_type,
                        category,
                        importance,
                        source,
                        project_id,
                        created_at,
                        updated_at,
                        embedding IS NOT NULL AS has_embedding
                    FROM memories
                    ORDER BY id
                    LIMIT %s OFFSET %s;
                    """,
                    (limit, offset),
                ).fetchall()

        return {
            "count": len(rows),
            "memories": [
                {
                    "id": row[0],
                    "content": row[1],
                    "memory_type": row[2],
                    "category": row[3],
                    "importance": row[4],
                    "source": row[5],
                    "project_id": row[6],
                    "created_at": row[7],
                    "updated_at": row[8],
                    "has_embedding": row[9],
                }
                for row in rows
            ],
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------
# Semantic search
# -------------------------

@router.get("/search")
def search_memories(
    q: str,
    limit: int = 5,
    project_id: int | None = None,
):
    try:
        limit = max(1, min(limit, 20))

        query_embedding = Vector(
            model.encode(
                "query: " + q,
                normalize_embeddings=True,
            ).tolist()
        )

        with get_db_connection() as conn:
            if project_id is not None:
                rows = conn.execute(
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
                rows = conn.execute(
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

        return {
            "query": q,
            "project_id": project_id,
            "results": [
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
                for row in rows
            ],
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# -------------------------
# Get one memory
# -------------------------

@router.get("/{memory_id}")
def get_memory(memory_id: int):
    try:
        with get_db_connection() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    content,
                    memory_type,
                    category,
                    importance,
                    source,
                    project_id,
                    created_at,
                    updated_at,
                    embedding IS NOT NULL AS has_embedding
                FROM memories
                WHERE id = %s;
                """,
                (memory_id,),
            ).fetchone()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail="Memory not found",
            )

        return {
            "id": row[0],
            "content": row[1],
            "memory_type": row[2],
            "category": row[3],
            "importance": row[4],
            "source": row[5],
            "project_id": row[6],
            "created_at": row[7],
            "updated_at": row[8],
            "has_embedding": row[9],
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------
# Update memory
# -------------------------

@router.patch("/{memory_id}")
def update_memory(memory_id: int, memory: MemoryUpdate):
    try:
        with get_db_connection() as conn:
            current = conn.execute(
                """
                SELECT
                    content,
                    memory_type,
                    category,
                    importance,
                    source,
                    project_id
                FROM memories
                WHERE id = %s;
                """,
                (memory_id,),
            ).fetchone()

            if current is None:
                raise HTTPException(
                    status_code=404,
                    detail="Memory not found",
                )

            content = (
                memory.content
                if memory.content is not None
                else current[0]
            )

            memory_type = (
                memory.memory_type
                if memory.memory_type is not None
                else current[1]
            )

            category = (
                memory.category
                if memory.category is not None
                else current[2]
            )

            importance = (
                memory.importance
                if memory.importance is not None
                else current[3]
            )

            source = (
                memory.source
                if memory.source is not None
                else current[4]
            )

            project_id = (
                memory.project_id
                if memory.project_id is not None
                else current[5]
            )

            if project_id is not None:
                project = conn.execute(
                    """
                    SELECT id
                    FROM projects
                    WHERE id = %s;
                    """,
                    (project_id,),
                ).fetchone()

                if project is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Project not found",
                    )


            # 내용이 바뀌었을 수도 있으므로 항상 embedding 재생성
            embedding = Vector(
                model.encode(
                    "passage: " + content,
                    normalize_embeddings=True,
                ).tolist()
            )

            row = conn.execute(
                """
                UPDATE memories
                SET
                    content = %s,
                    memory_type = %s,
                    category = %s,
                    importance = %s,
                    source = %s,
                    embedding = %s,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING
                    id,
                    content,
                    memory_type,
                    category,
                    importance,
                    source,
                    updated_at;
                """,
                (
                    content,
                    memory_type,
                    category,
                    importance,
                    source,
                    embedding,
                    memory_id,
                ),
            ).fetchone()

            conn.commit()

        return {
            "status": "updated",
            "id": row[0],
            "content": row[1],
            "memory_type": row[2],
            "category": row[3],
            "importance": row[4],
            "source": row[5],
            "updated_at": row[6],
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------
# Delete memory
# -------------------------

@router.delete("/{memory_id}")
def delete_memory(memory_id: int):
    try:
        with get_db_connection() as conn:
            row = conn.execute(
                """
                DELETE FROM memories
                WHERE id = %s
                RETURNING id;
                """,
                (memory_id,),
            ).fetchone()

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Memory not found",
                )

            conn.commit()

        return {
            "status": "deleted",
            "id": row[0],
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))