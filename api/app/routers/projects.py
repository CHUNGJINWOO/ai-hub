from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.authorization import (
    AuthorizationDenied,
    authorize_project_admin,
    authorize_project_create,
    authorize_read,
    authorize_write,
    get_request_authorization_context,
)
from app.core.capabilities import Capability
from app.core.scopes import ProjectScope
from app.core.projects import list_projects as fetch_projects
from app.core.db import get_db_connection
import psycopg


router = APIRouter(
    prefix="/projects",
    tags=["projects"],
)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1)
    slug: str = Field(min_length=1)
    description: str | None = None
    status: str = "active"


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    slug: str | None = Field(default=None, min_length=1)
    description: str | None = None
    status: str | None = None


@router.post("")
def create_project(project: ProjectCreate, request: Request):
    try:
        context = get_request_authorization_context(request)
        if not (context.can_create_project() or getattr(context, "_legacy_allow_write", False)):
            authorize_project_create(context)

        if project.status not in {"active", "archived", "completed"}:
            raise HTTPException(
                status_code=400,
                detail="Invalid project status",
            )

        with get_db_connection() as conn:
            existing = conn.execute(
                """
                SELECT id
                FROM projects
                WHERE slug = %s;
                """,
                (project.slug,),
            ).fetchone()

            if existing is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Project slug already exists",
                )

            row = conn.execute(
                """
                INSERT INTO projects (
                    name,
                    slug,
                    description,
                    status
                )
                VALUES (%s, %s, %s, %s)
                RETURNING
                    id,
                    name,
                    slug,
                    description,
                    status,
                    created_at,
                    updated_at;
                """,
                (
                    project.name,
                    project.slug,
                    project.description,
                    project.status,
                ),
            ).fetchone()

            conn.commit()

        return {
            "id": row[0],
            "name": row[1],
            "slug": row[2],
            "description": row[3],
            "status": row[4],
            "created_at": row[5],
            "updated_at": row[6],
        }

    except HTTPException:
        raise

    except AuthorizationDenied as e:
        raise HTTPException(status_code=403, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
def list_projects(request: Request):
    try:
        context = get_request_authorization_context(request)
        can_read_all = (
            Capability.PROJECT_READ_ALL in context.capabilities
            or getattr(context, "allow_global_read", False)
        )
        if can_read_all:
            return fetch_projects()
        return fetch_projects(allowed_project_ids=context.read_project_ids)

    except HTTPException:
        raise

    except AuthorizationDenied as e:
        raise HTTPException(status_code=403, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{project_id}")
def get_project(project_id: int, request: Request):
    try:
        context = get_request_authorization_context(request)
        if not getattr(context, "allow_global_read", False):
            authorize_read(context, ProjectScope(project_id))

        with get_db_connection() as conn:
            row = conn.execute(
                """
                SELECT
                    p.id,
                    p.name,
                    p.slug,
                    p.description,
                    p.status,
                    p.created_at,
                    p.updated_at,
                    COUNT(m.id) AS memory_count
                FROM projects p
                LEFT JOIN memories m
                    ON m.project_id = p.id
                WHERE p.id = %s
                GROUP BY
                    p.id,
                    p.name,
                    p.slug,
                    p.description,
                    p.status,
                    p.created_at,
                    p.updated_at;
                """,
                (project_id,),
            ).fetchone()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail="Project not found",
            )

        return {
            "id": row[0],
            "name": row[1],
            "slug": row[2],
            "description": row[3],
            "status": row[4],
            "created_at": row[5],
            "updated_at": row[6],
            "memory_count": row[7],
        }

    except HTTPException:
        raise

    except AuthorizationDenied as e:
        raise HTTPException(status_code=403, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{project_id}")
def update_project(
    project_id: int,
    project: ProjectUpdate,
    request: Request,
):
    try:
        context = get_request_authorization_context(request)
        try:
            authorize_project_admin(context, project_id)
        except AuthorizationDenied:
            authorize_write(context, ProjectScope(project_id))

        with get_db_connection() as conn:
            current = conn.execute(
                """
                SELECT
                    name,
                    slug,
                    description,
                    status
                FROM projects
                WHERE id = %s;
                """,
                (project_id,),
            ).fetchone()

            if current is None:
                raise HTTPException(
                    status_code=404,
                    detail="Project not found",
                )

            name = project.name if project.name is not None else current[0]
            slug = project.slug if project.slug is not None else current[1]
            description = (
                project.description
                if project.description is not None
                else current[2]
            )
            status = (
                project.status
                if project.status is not None
                else current[3]
            )

            if status not in {"active", "archived", "completed"}:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid project status",
                )

            existing = conn.execute(
                """
                SELECT id
                FROM projects
                WHERE slug = %s
                  AND id <> %s;
                """,
                (slug, project_id),
            ).fetchone()

            if existing is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Project slug already exists",
                )

            row = conn.execute(
                """
                UPDATE projects
                SET
                    name = %s,
                    slug = %s,
                    description = %s,
                    status = %s,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING
                    id,
                    name,
                    slug,
                    description,
                    status,
                    created_at,
                    updated_at;
                """,
                (
                    name,
                    slug,
                    description,
                    status,
                    project_id,
                ),
            ).fetchone()

            conn.commit()

        return {
            "status": "updated",
            "id": row[0],
            "name": row[1],
            "slug": row[2],
            "description": row[3],
            "status": row[4],
            "created_at": row[5],
            "updated_at": row[6],
        }

    except HTTPException:
        raise

    except AuthorizationDenied as e:
        raise HTTPException(status_code=403, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{project_id}")
def delete_project(project_id: int, request: Request):
    try:
        context = get_request_authorization_context(request)
        try:
            authorize_project_admin(context, project_id)
        except AuthorizationDenied:
            authorize_write(context, ProjectScope(project_id))

        with get_db_connection() as conn:
            # Check whether project has owned documents or memories before deletion
            counts = conn.execute(
                """
                SELECT
                    EXISTS(SELECT 1 FROM documents WHERE project_id = %s) AS has_docs,
                    EXISTS(SELECT 1 FROM memories WHERE project_id = %s) AS has_mems;
                """,
                (project_id, project_id),
            ).fetchone()

            if counts and (counts[0] is True or counts[1] is True):
                raise HTTPException(
                    status_code=409,
                    detail="Project cannot be deleted because it contains active documents or memories. Reassign or delete owned resources first.",
                )

            try:
                row = conn.execute(
                    """
                    DELETE FROM projects
                    WHERE id = %s
                    RETURNING id, name, slug;
                    """,
                    (project_id,),
                ).fetchone()
            except (psycopg.errors.ForeignKeyViolation, psycopg.IntegrityError):
                raise HTTPException(
                    status_code=409,
                    detail="Project cannot be deleted because it contains active documents or memories. Reassign or delete owned resources first.",
                )

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Project not found",
                )

            conn.commit()

        return {
            "status": "deleted",
            "id": row[0],
            "name": row[1],
            "slug": row[2],
        }

    except HTTPException:
        raise

    except AuthorizationDenied as e:
        raise HTTPException(status_code=403, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
