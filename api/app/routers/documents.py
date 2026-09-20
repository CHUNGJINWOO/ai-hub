import hashlib
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from pgvector import Vector

from app.core.db import get_db_connection
from app.core.document_ingest import chunk_pages, extract_text
from app.core.embedding import model

from app.core.search import (
    create_query_embedding,
    search_documents as search_document_chunks,
)

from app.core.documents import get_document as fetch_document

router = APIRouter(
    prefix="/documents",
    tags=["documents"],
)


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1)
    filename: str | None = None
    mime_type: str | None = None
    source: str | None = None
    description: str | None = None
    status: str = "active"
    project_id: int | None = None


class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    filename: str | None = None
    mime_type: str | None = None
    source: str | None = None
    description: str | None = None
    status: str | None = None
    project_id: int | None = None


class DocumentChunkCreate(BaseModel):
    content: str = Field(min_length=1)
    chunk_index: int = Field(ge=0)
    page_number: int | None = Field(default=None, ge=1)


# -------------------------
# Basic endpoints
# -------------------------

@router.post("")
def create_document(document: DocumentCreate):
    try:
        if document.status not in {"active", "archived"}:
            raise HTTPException(
                status_code=400,
                detail="Invalid document status",
            )

        with get_db_connection() as conn:
            if document.project_id is not None:
                project = conn.execute(
                    """
                    SELECT id
                    FROM projects
                    WHERE id = %s;
                    """,
                    (document.project_id,),
                ).fetchone()

                if project is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Project not found",
                    )

            row = conn.execute(
                """
                INSERT INTO documents (
                    project_id,
                    title,
                    filename,
                    mime_type,
                    source,
                    description,
                    status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING
                    id,
                    project_id,
                    title,
                    filename,
                    mime_type,
                    source,
                    description,
                    status,
                    created_at,
                    updated_at;
                """,
                (
                    document.project_id,
                    document.title,
                    document.filename,
                    document.mime_type,
                    document.source,
                    document.description,
                    document.status,
                ),
            ).fetchone()

            conn.commit()

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
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
def list_documents(project_id: int | None = None):
    try:
        with get_db_connection() as conn:
            if project_id is not None:
                rows = conn.execute(
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
                    WHERE d.project_id = %s
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
                        d.updated_at
                    ORDER BY d.id;
                    """,
                    (project_id,),
                ).fetchall()
            else:
                rows = conn.execute(
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
                        d.updated_at
                    ORDER BY d.id;
                    """
                ).fetchall()

        return {
            "count": len(rows),
            "documents": [
                {
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
                for row in rows
            ],
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{document_id}")
def get_document(document_id: int):
    try:
        result = fetch_document(document_id)

        if result is None:
            raise HTTPException(
                status_code=404,
                detail="Document not found",
            )

        return result

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )


# -------------------------
# Document semantic search
# -------------------------

@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    project_id: int | None = None,
    title: str | None = Form(default=None),
):
    try:
        if not file.filename:
            raise HTTPException(
                status_code=400,
                detail="Filename is required",
            )

        suffix = Path(file.filename).suffix.lower()

        allowed_suffixes = {".pdf", ".txt", ".md", ".markdown", ".py", ".cpp", ".cc", ".c", ".h", ".hpp", ".xml", ".yaml", ".yml", ".json", ".xacro", ".rviz", ".docx", ".xlsx", ".pptx"}

        if suffix not in allowed_suffixes:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Supported file types: PDF, TXT, Markdown, Python, "
                    "C/C++, headers, XML, YAML, JSON, Xacro, RViz, "
                    "DOCX, XLSX, PPTX"
                ),
            )

        with get_db_connection() as conn:
            if project_id is not None:
                project = conn.execute(
                    """
                    SELECT id
                    FROM projects
                    WHERE id = %s;
                    """,
                    (project_id,),
                ).fetchone()

                if project is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Project not found",
                    )

        upload_root = Path("/data/documents")
        upload_root.mkdir(parents=True, exist_ok=True)

        safe_name = Path(file.filename).name
        raw_bytes = await file.read()

        if not raw_bytes:
            raise HTTPException(
                status_code=400,
                detail="Uploaded file is empty",
            )

        sha256 = hashlib.sha256(raw_bytes).hexdigest()

        target_name = f"{sha256}_{safe_name}"
        target_path = upload_root / target_name

        target_path.write_bytes(raw_bytes)

        try:
            pages = extract_text(
                target_path,
                file.content_type,
            )
        except ValueError as e:
            target_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=400,
                detail=str(e),
            )

        chunks = chunk_pages(pages)

        if not chunks:
            target_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=400,
                detail="No extractable text found in the document",
            )

        embeddings = []

        for _, _, content in chunks:
            embedding = Vector(
                model.encode(
                    "passage: " + content,
                    normalize_embeddings=True,
                ).tolist()
            )
            embeddings.append(embedding)

        document_title = title or Path(file.filename).stem

        with get_db_connection() as conn:
            document_row = conn.execute(
                """
                INSERT INTO documents (
                    project_id,
                    title,
                    filename,
                    mime_type,
                    source,
                    description,
                    status,
                    file_path,
                    file_size,
                    sha256,
                    document_type,
                    relative_path,
                    file_hash
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, 'active',
                    %s, %s, %s, %s, %s, %s
                )
                RETURNING id;
                """,
                (
                    project_id,
                    document_title,
                    safe_name,
                    file.content_type,
                    "upload",
                    None,
                    str(target_path),
                    len(raw_bytes),
                    sha256,
                    suffix.lstrip("."),
                    safe_name,
                    sha256,
                ),
            ).fetchone()

            document_id = document_row[0]

            for (chunk_index, page_number, content), embedding in zip(
                chunks,
                embeddings,
            ):
                conn.execute(
                    """
                    INSERT INTO document_chunks (
                        document_id,
                        chunk_index,
                        content,
                        page_number,
                        embedding
                    )
                    VALUES (%s, %s, %s, %s, %s);
                    """,
                    (
                        document_id,
                        chunk_index,
                        content,
                        page_number,
                        embedding,
                    ),
                )

            conn.commit()

        return {
            "status": "created",
            "document_id": document_id,
            "project_id": project_id,
            "filename": safe_name,
            "file_size": len(raw_bytes),
            "sha256": sha256,
            "chunk_count": len(chunks),
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )


@router.get("/search")
def search_documents(
    q: str,
    limit: int = 5,
    project_id: int | None = None,
):
    try:
        if not q.strip():
            raise HTTPException(
                status_code=400,
                detail="Query must not be empty",
            )

        limit = max(1, min(limit, 20))

        query_embedding = create_query_embedding(q)

        results = search_document_chunks(
            query=q,
            query_embedding=query_embedding,
            limit=limit,
            project_id=project_id,
        )

        return {
            "query": q,
            "project_id": project_id,
            "results": results,
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )
    


@router.patch("/{document_id}")
def update_document(document_id: int, document: DocumentUpdate):
    try:
        with get_db_connection() as conn:
            current = conn.execute(
                """
                SELECT
                    project_id,
                    title,
                    filename,
                    mime_type,
                    source,
                    description,
                    status
                FROM documents
                WHERE id = %s;
                """,
                (document_id,),
            ).fetchone()

            if current is None:
                raise HTTPException(
                    status_code=404,
                    detail="Document not found",
                )

            project_id = (
                document.project_id
                if document.project_id is not None
                else current[0]
            )

            title = (
                document.title
                if document.title is not None
                else current[1]
            )

            filename = (
                document.filename
                if document.filename is not None
                else current[2]
            )

            mime_type = (
                document.mime_type
                if document.mime_type is not None
                else current[3]
            )

            source = (
                document.source
                if document.source is not None
                else current[4]
            )

            description = (
                document.description
                if document.description is not None
                else current[5]
            )

            status = (
                document.status
                if document.status is not None
                else current[6]
            )

            if status not in {"active", "archived"}:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid document status",
                )

            if project_id is not None:
                project = conn.execute(
                    """
                    SELECT id
                    FROM projects
                    WHERE id = %s;
                    """,
                    (project_id,),
                ).fetchone()

                if project is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Project not found",
                    )

            row = conn.execute(
                """
                UPDATE documents
                SET
                    project_id = %s,
                    title = %s,
                    filename = %s,
                    mime_type = %s,
                    source = %s,
                    description = %s,
                    status = %s,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING
                    id,
                    project_id,
                    title,
                    filename,
                    mime_type,
                    source,
                    description,
                    status,
                    created_at,
                    updated_at;
                """,
                (
                    project_id,
                    title,
                    filename,
                    mime_type,
                    source,
                    description,
                    status,
                    document_id,
                ),
            ).fetchone()

            conn.commit()

        return {
            "status": "updated",
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
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{document_id}")
def delete_document(document_id: int):
    try:
        with get_db_connection() as conn:
            row = conn.execute(
                """
                DELETE FROM documents
                WHERE id = %s
                RETURNING id, title;
                """,
                (document_id,),
            ).fetchone()

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Document not found",
                )

            conn.commit()

        return {
            "status": "deleted",
            "id": row[0],
            "title": row[1],
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{document_id}/chunks")
def create_document_chunk(
    document_id: int,
    chunk: DocumentChunkCreate,
):
    try:
        embedding = Vector(
            model.encode(
                "passage: " + chunk.content,
                normalize_embeddings=True,
            ).tolist()
        )

        with get_db_connection() as conn:
            document = conn.execute(
                """
                SELECT id
                FROM documents
                WHERE id = %s;
                """,
                (document_id,),
            ).fetchone()

            if document is None:
                raise HTTPException(
                    status_code=404,
                    detail="Document not found",
                )

            existing = conn.execute(
                """
                SELECT id
                FROM document_chunks
                WHERE document_id = %s
                  AND chunk_index = %s;
                """,
                (document_id, chunk.chunk_index),
            ).fetchone()

            if existing is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Chunk index already exists for this document",
                )

            row = conn.execute(
                """
                INSERT INTO document_chunks (
                    document_id,
                    chunk_index,
                    content,
                    page_number,
                    embedding
                )
                VALUES (%s, %s, %s, %s, %s)
                RETURNING
                    id,
                    document_id,
                    chunk_index,
                    content,
                    page_number,
                    created_at;
                """,
                (
                    document_id,
                    chunk.chunk_index,
                    chunk.content,
                    chunk.page_number,
                    embedding,
                ),
            ).fetchone()

            conn.commit()

        return {
            "status": "created",
            "id": row[0],
            "document_id": row[1],
            "chunk_index": row[2],
            "content": row[3],
            "page_number": row[4],
            "created_at": row[5],
            "has_embedding": True,
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{document_id}/chunks")
def list_document_chunks(document_id: int):
    try:
        with get_db_connection() as conn:
            document = conn.execute(
                """
                SELECT id
                FROM documents
                WHERE id = %s;
                """,
                (document_id,),
            ).fetchone()

            if document is None:
                raise HTTPException(
                    status_code=404,
                    detail="Document not found",
                )

            rows = conn.execute(
                """
                SELECT
                    id,
                    document_id,
                    chunk_index,
                    content,
                    page_number,
                    created_at,
                    embedding IS NOT NULL AS has_embedding
                FROM document_chunks
                WHERE document_id = %s
                ORDER BY chunk_index;
                """,
                (document_id,),
            ).fetchall()

        return {
            "document_id": document_id,
            "count": len(rows),
            "chunks": [
                {
                    "id": row[0],
                    "document_id": row[1],
                    "chunk_index": row[2],
                    "content": row[3],
                    "page_number": row[4],
                    "created_at": row[5],
                    "has_embedding": row[6],
                }
                for row in rows
            ],
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

