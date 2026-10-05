from app.core.db import get_db_connection


def list_projects(
    allowed_project_ids: frozenset[int] | set[int] | None = None,
) -> dict:
    with get_db_connection() as conn:
        if allowed_project_ids is not None:
            if not allowed_project_ids:
                return {"count": 0, "projects": []}

            project_ids_tuple = tuple(allowed_project_ids)
            rows = conn.execute(
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
                WHERE p.id = ANY(%s)
                GROUP BY
                    p.id,
                    p.name,
                    p.slug,
                    p.description,
                    p.status,
                    p.created_at,
                    p.updated_at
                ORDER BY p.id;
                """,
                (list(project_ids_tuple),),
            ).fetchall()
        else:
            rows = conn.execute(
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
            ).fetchall()

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
