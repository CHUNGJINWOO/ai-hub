from app.core.db import get_db_connection


def list_projects() -> dict:
    with get_db_connection() as conn:
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
