import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from pgvector import Vector
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.core.authorization import AuthorizationContext
from app.core.db import (
    ensure_memories_table,
    ensure_projects_table,
    get_db_connection,
)
from app.core.embedding import model
from app.core.token_verifier import verify_rest_token

from app.routers.projects import router as projects_router
from app.routers.memories import router as memories_router
from app.routers.documents import router as documents_router
from app.routers.context import router as context_router

# ---------------------------------------------------------------------------
# REST Authorization Middleware
# ---------------------------------------------------------------------------

_REST_API_KEY: str | None = os.getenv("MCP_ACCESS_TOKEN")


class RestAuthorizationMiddleware(BaseHTTPMiddleware):
    """Extract a Bearer token and inject an AuthorizationContext into
    request.state so that existing require_bound_request_project_access()
    guards can enforce project-level access without modification.

    If no token is present or verification fails the middleware leaves
    request.state.authorization_context unset.  Guards on individual routes
    will then raise AuthorizationDenied (→ HTTP 403), preserving the
    fail-closed contract already tested in test_context_http.py.
    """

    async def dispatch(self, request: Request, call_next: object) -> Response:
        auth_header: str = request.headers.get("Authorization", "")

        if auth_header.startswith("Bearer "):
            raw_token = auth_header[len("Bearer "):].strip()
            ctx: AuthorizationContext | None = verify_rest_token(
                raw_token,
                api_key=_REST_API_KEY,
            )
            if ctx is not None:
                # Only set when we have a verified context; do not overwrite a
                # pre-existing value that may have been injected via app_state
                # (used by TestClient in tests).
                if not getattr(request.state, "authorization_context", None):
                    request.state.authorization_context = ctx

        return await call_next(request)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_memories_table()
    ensure_projects_table()
    yield


app = FastAPI(
    title="AI Knowledge Hub",
    lifespan=lifespan,
)

app.add_middleware(RestAuthorizationMiddleware)

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
