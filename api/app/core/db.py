import os

import psycopg
from pgvector.psycopg import register_vector


def get_db_connection():
    conn = psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
    )

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


def ensure_projects_table():
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                description TEXT,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'archived', 'completed')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

        conn.execute(
            """
            ALTER TABLE memories
            ADD COLUMN IF NOT EXISTS project_id BIGINT
            REFERENCES projects(id)
            ON DELETE SET NULL;
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_memories_project_id
            ON memories(project_id);
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_projects_slug
            ON projects(slug);
            """
        )

        conn.commit()
