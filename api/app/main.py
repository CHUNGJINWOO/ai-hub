from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from pgvector import Vector

from app.core.db import (
    ensure_memories_table,
    ensure_projects_table,
    get_db_connection,
)
from app.core.embedding import model

from app.routers.projects import router as projects_router
from app.routers.memories import router as memories_router
from app.routers.documents import router as documents_router
from app.routers.context import router as context_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_memories_table()
    ensure_projects_table()
    yield


app = FastAPI(
    title="AI Knowledge Hub",
    lifespan=lifespan,
)

app.include_router(projects_router)
app.include_router(memories_router)
app.include_router(documents_router)
app.include_router(context_router)

@app.get("/healthz", tags=["ops"])
def healthz():
    try:
        with get_db_connection() as conn:
            conn.execute("SELECT 1")

        return {
            "status": "ok",
            "service": "ai-hub-api",
            "database": "ok",
        }

    except Exception:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unhealthy",
                "service": "ai-hub-api",
                "database": "unavailable",
            },
        )

# -------------------------
# Request models
# -------------------------
