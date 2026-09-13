import os
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from pgvector import Vector
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer


MODEL_NAME = "intfloat/multilingual-e5-small"

model = SentenceTransformer(MODEL_NAME)


def get_db_connection():
    conn = psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
    )

    # Psycopg 3에서 pgvector 타입을 등록
    register_vector(conn)

    return conn


def ensure_memories_table():
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id BIGSERIAL PRIMARY KEY,
                content TEXT NOT NULL,
                memory_type TEXT NOT NULL DEFAULT 'fact',
                category TEXT,
                importance INTEGER NOT NULL DEFAULT 3
                    CHECK (importance BETWEEN 1 AND 5),
                source TEXT,
                embedding VECTOR(384),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_memories_table()
    yield


app = FastAPI(
    title="AI Knowledge Hub",
    lifespan=lifespan,
)


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1)
    memory_type: str = "fact"
    category: str | None = None
    importance: int = Field(default=3, ge=1, le=5)
    source: str | None = "api"


@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "ai-hub-api",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
    }


@app.get("/health/db")
def health_db():
    try:
        with get_db_connection() as conn:
            result = conn.execute("SELECT 1;").fetchone()

        return {
            "status": "healthy",
            "database": "connected",
            "test": result[0],
        }

    except Exception as e:
        return {
            "status": "unhealthy",
            "database": "disconnected",
            "error": str(e),
        }


@app.post("/memories")
def create_memory(memory: MemoryCreate):
    try:
        embedding = Vector(
            model.encode(
                "passage: " + memory.content,
                normalize_embeddings=True,
            ).tolist()
        )

        with get_db_connection() as conn:
            result = conn.execute(
                """
                INSERT INTO memories (
                    content,
                    memory_type,
                    category,
                    importance,
                    source,
                    embedding
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (
                    memory.content,
                    memory.memory_type,
                    memory.category,
                    memory.importance,
                    memory.source,
                    embedding,
                ),
            )

            memory_id = result.fetchone()[0]
            conn.commit()

        return {
            "status": "created",
            "id": memory_id,
            "content": memory.content,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/memories/search")
def search_memories(q: str, limit: int = 5):
    try:
        limit = max(1, min(limit, 20))

        query_embedding = Vector(
            model.encode(
                "query: " + q,
                normalize_embeddings=True,
            ).tolist()
        )

        with get_db_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    content,
                    memory_type,
                    category,
                    importance,
                    source,
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
            "results": [
                {
                    "id": row[0],
                    "content": row[1],
                    "memory_type": row[2],
                    "category": row[3],
                    "importance": row[4],
                    "source": row[5],
                    "distance": row[6],
                }
                for row in rows
            ],
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
