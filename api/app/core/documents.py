from app.core.db import get_db_connection


def get_document(document_id: int) -> dict | None:
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT
                d.id,
                d.project_id,
                d.title,
                d.filename,
                d.mime_type,
                d.source,
                d.description,
                d.status,
                d.created_at,
                d.updated_at,
                COUNT(c.id) AS chunk_count
            FROM documents d
            LEFT JOIN document_chunks c
                ON c.document_id = d.id
            WHERE d.id = %s
            GROUP BY
                d.id,
                d.project_id,
                d.title,
                d.filename,
                d.mime_type,
                d.source,
                d.description,
                d.status,
                d.created_at,
                d.updated_at;
            """,
            (document_id,),
        ).fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "project_id": row[1],
        "title": row[2],
        "filename": row[3],
        "mime_type": row[4],
        "source": row[5],
        "description": row[6],
        "status": row[7],
        "created_at": row[8],
        "updated_at": row[9],
        "chunk_count": row[10],
    }
