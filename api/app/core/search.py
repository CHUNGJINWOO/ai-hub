from __future__ import annotations

from pgvector import Vector

from app.core.db import get_db_connection
from app.core.embedding import model


CODE_INTENT_WORDS = {
    "코드",
    "구현",
    "함수",
    "클래스",
    "파일",
    "소스",
    "노드",
    "콜백",
    "토픽",
    "퍼블리셔",
    "서브스크라이버",
    "launch",
    "topic",
    "publisher",
    "subscriber",
    "callback",
    "function",
    "class",
    "source",
    "driver",
    "serial",
    "cmd_vel",
    "xacro",
    "cpp",
    "python",
}

DOCUMENTATION_WORDS = {
    "설명",
    "개요",
    "문서",
    "README",
    "overview",
    "documentation",
}

CODE_EXTENSIONS = {
    ".py",
    ".cpp",
    ".cc",
    ".c",
    ".h",
    ".hpp",
    ".xml",
    ".xacro",
    ".yaml",
    ".yml",
    ".rviz",
}


def create_query_embedding(query: str) -> Vector:
    return Vector(
        model.encode(
            "query: " + query,
            normalize_embeddings=True,
        ).tolist()
    )


def _query_tokens(query: str) -> list[str]:
    return [
        token.strip(".,!?()[]{}:/")
        for token in query.lower().split()
        if len(token.strip(".,!?()[]{}:/")) >= 2
    ]


def _detect_search_intent(query: str) -> tuple[bool, bool]:
    query_lower = query.lower()

    code_intent = any(
        word in query_lower
        for word in CODE_INTENT_WORDS
    )

    documentation_intent = any(
        word in query_lower
        for word in DOCUMENTATION_WORDS
    )

    return code_intent, documentation_intent


def search_documents(
    query: str,
    query_embedding: Vector,
    limit: int = 5,
    project_id: int | None = None,
) -> list[dict]:
    limit = max(1, min(limit, 20))
    candidate_limit = min(max(limit * 5, 20), 100)

    with get_db_connection() as conn:
        if project_id is not None:
            rows = conn.execute(
                """
                SELECT
                    c.id,
                    c.document_id,
                    d.project_id,
                    d.title,
                    d.filename,
                    d.source,
                    d.document_type,
                    d.relative_path,
                    c.chunk_index,
                    c.page_number,
                    c.content,
                    c.embedding <=> %s AS distance
                FROM document_chunks c
                JOIN documents d
                    ON d.id = c.document_id
                WHERE c.embedding IS NOT NULL
                  AND d.project_id = %s
                ORDER BY c.embedding <=> %s
                LIMIT %s;
                """,
                (
                    query_embedding,
                    project_id,
                    query_embedding,
                    candidate_limit,
                ),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                    c.id,
                    c.document_id,
                    d.project_id,
                    d.title,
                    d.filename,
                    d.source,
                    d.document_type,
                    d.relative_path,
                    c.chunk_index,
                    c.page_number,
                    c.content,
                    c.embedding <=> %s AS distance
                FROM document_chunks c
                JOIN documents d
                    ON d.id = c.document_id
                WHERE c.embedding IS NOT NULL
                ORDER BY c.embedding <=> %s
                LIMIT %s;
                """,
                (
                    query_embedding,
                    query_embedding,
                    candidate_limit,
                ),
            ).fetchall()

    code_intent, documentation_intent = _detect_search_intent(query)
    query_tokens = _query_tokens(query)

    reranked: list[tuple[float, dict]] = []

    for row in rows:
        (
            chunk_id,
            document_id,
            row_project_id,
            title,
            filename,
            source,
            document_type,
            relative_path,
            chunk_index,
            page_number,
            content,
            distance,
        ) = row

        filename_lower = (filename or "").lower()
        title_lower = (title or "").lower()
        content_lower = (content or "").lower()

        keyword_score = 0.0

        for token in query_tokens:
            if token in filename_lower:
                keyword_score += 0.30

            if token in title_lower:
                keyword_score += 0.20

            if token in content_lower:
                keyword_score += 0.05

        extension = ""
        if "." in filename_lower:
            extension = "." + filename_lower.rsplit(".", 1)[1]

        file_type_boost = 0.0

        if code_intent and extension in CODE_EXTENSIONS:
            file_type_boost += 0.25

        if code_intent and filename_lower.startswith("readme"):
            file_type_boost -= 0.25

        if documentation_intent and filename_lower.startswith("readme"):
            file_type_boost += 0.20

        final_score = (
            float(distance)
            - keyword_score
            - file_type_boost
        )

        reranked.append(
            (
                final_score,
                {
                    "chunk_id": chunk_id,
                    "document_id": document_id,
                    "project_id": row_project_id,
                    "title": title,
                    "filename": filename,
                    "source": source,
                    "document_type": document_type,
                    "relative_path": relative_path,
                    "chunk_index": chunk_index,
                    "page_number": page_number,
                    "content": content,
                    "distance": distance,
                    "hybrid_score": final_score,
                },
            )
        )

    reranked.sort(key=lambda item: item[0])

    return [
        item[1]
        for item in reranked[:limit]
    ]


def search_memories(
    query_embedding: Vector,
    limit: int = 5,
    project_id: int | None = None,
) -> list[dict]:
    limit = max(1, min(limit, 20))

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

    return [
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
    ]


def search_context(
    query: str,
    limit: int = 5,
    project_id: int | None = None,
) -> dict:
    if not query.strip():
        raise ValueError("Query must not be empty")

    limit = max(1, min(limit, 10))

    query_embedding = create_query_embedding(query)

    memories = search_memories(
        query_embedding=query_embedding,
        limit=limit,
        project_id=project_id,
    )

    documents = search_documents(
        query=query,
        query_embedding=query_embedding,
        limit=limit,
        project_id=project_id,
    )

    return {
        "query": query,
        "project_id": project_id,
        "memory_count": len(memories),
        "document_count": len(documents),
        "memories": memories,
        "documents": documents,
    }