from collections.abc import Collection

from app.core.db import get_db_connection


def list_projects(
    allowed_project_ids: Collection[int] | None = None,
) -> dict:
    if allowed_project_ids is not None and not allowed_project_ids:
        return {"count": 0, "projects": []}

    query = """
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
    """
    params: tuple = ()

    if allowed_project_ids is not None:
        query += " WHERE p.id = ANY(%s) "
        params = (list(allowed_project_ids),)

    query += """
        GROUP BY
            p.id,
            p.name,
            p.slug,
            p.description,
            p.status,
            p.created_at,
            p.updated_at
        ORDER BY p.id;
    """

    with get_db_connection() as conn:
        if params:
            rows = conn.execute(query, params).fetchall()
        else:
            rows = conn.execute(query).fetchall()

    return {
        "count": len(rows),
        "projects": [
            {
                "id": row[0],
                "name": row[1],
                "slug": row[2],
                "description": row[3],
                "status": row[4],
                "created_at": row[5],
                "updated_at": row[6],
                "memory_count": row[7],
            }
            for row in rows
        ],
    }
