import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import urllib.parse
import uuid

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from pgvector import Vector

from app.core.db import get_db_connection
from app.core.document_ingest import chunk_pages, extract_text
from app.core.embedding import model
from app.core.authorization import (
    AuthorizationDenied,
    authorize_read,
    authorize_write,
    get_request_authorization_context,
)
from app.core.capabilities import Capability
from app.core.scopes import GlobalScope, ProjectScope

from app.core.search import (
    create_query_embedding,
    search_documents as search_document_chunks,
)

from app.core.documents import get_document as fetch_document

ALLOWED_GITHUB_REPOS = {
    "learning-portfolio",
    "ai-hub",
    "guidebook",
    "ros2",
}

FORBIDDEN_FILE_PATTERNS = [
    re.compile(r"^\.env(\..*)?$", re.IGNORECASE),
    re.compile(r"^.*\.env$", re.IGNORECASE),
    re.compile(r"^.*\.pem$", re.IGNORECASE),
    re.compile(r"^.*\.key$", re.IGNORECASE),
    re.compile(r"^id_rsa.*$", re.IGNORECASE),
    re.compile(r"^id_ed25519.*$", re.IGNORECASE),
    re.compile(r"^rclone\.conf$", re.IGNORECASE),
    re.compile(r"^.*\.bag$", re.IGNORECASE),
    re.compile(r"^.*\.db3$", re.IGNORECASE),
    # Secrets/credentials files (avoid matching code/docs like tokenizer.py or token_notes.md)
    re.compile(r"^.*_?token[s]?\.(json|txt|key|env|secret|conf)$", re.IGNORECASE),
    re.compile(r"^token[s]?$", re.IGNORECASE),
    re.compile(r"^.*_?credential[s]?\.(json|txt|key|env|secret|xml|conf)$", re.IGNORECASE),
    re.compile(r"^credential[s]?$", re.IGNORECASE),
    re.compile(r"^client_secret.*\.json$", re.IGNORECASE),
]

EXCLUDED_PATH_PREFIXES = (
    "inbox/",
    "school/",
    ".git/",
    "build/",
    "install/",
    "log/",
    "__pycache__/",
    "external/",
    "lectures/unverified/",
)

MAX_UPLOAD_SIZE_BYTES = int(os.environ.get("MAX_UPLOAD_SIZE_BYTES", str(50 * 1024 * 1024)))


def _validate_safe_filename(safe_name: str, relative_path: str | None = None) -> None:
    candidates = [safe_name]
    if relative_path:
        candidates.extend(relative_path.replace("\\", "/").split("/"))
    for item in candidates:
        if not item:
            continue
        for pattern in FORBIDDEN_FILE_PATTERNS:
            if pattern.match(item):
                raise HTTPException(
                    status_code=400,
                    detail=f"Sensitive or restricted file is not permitted: {item}",
                )


def _validate_and_normalize_path(relative_path: str | None, safe_name: str) -> str:
    if relative_path is None:
        return safe_name

    rel = relative_path.strip()
    if not rel:
        raise HTTPException(
            status_code=400,
            detail="relative_path must not be empty if provided",
        )

    # Reject absolute paths (POSIX and Windows)
    if rel.startswith("/") or rel.startswith("\\") or (len(rel) > 1 and rel[1] == ":"):
        raise HTTPException(
            status_code=400,
            detail="relative_path must be a relative path from the repository root",
        )

    # Normalize POSIX separators
    normalized = posixpath.normpath(rel.replace("\\", "/"))

    # Reject path traversal outside repository root
    if normalized == "." or normalized == ".." or normalized.startswith("../") or "/../" in ("/" + normalized + "/"):
        raise HTTPException(
            status_code=400,
            detail="relative_path contains path traversal or escapes repository root",
        )

    # Check indexing exclusion rules
    norm_lower = normalized.lower()
    norm_parts = norm_lower.split("/")
    for prefix in EXCLUDED_PATH_PREFIXES:
        clean_prefix = prefix.lower().rstrip("/")
        # Matches if normalized path starts with the prefix as a directory
        if norm_lower == clean_prefix or norm_lower.startswith(clean_prefix + "/"):
            raise HTTPException(
                status_code=400,
                detail=f"relative_path '{normalized}' matches excluded indexing pattern '{prefix}'",
            )
        # Matches if any individual directory segment matches (e.g. dir/inbox/file.md)
        if "/" not in clean_prefix and clean_prefix in norm_parts:
            raise HTTPException(
                status_code=400,
                detail=f"relative_path '{normalized}' matches excluded indexing pattern '{prefix}'",
            )

    # Validate that filename in relative_path matches the uploaded file's safe_name
    path_basename = posixpath.basename(normalized)
    if path_basename != safe_name:
        raise HTTPException(
            status_code=400,
            detail=f"relative_path filename '{path_basename}' does not match uploaded filename '{safe_name}'",
        )

    return normalized


def _validate_source_and_commit(
    source: str | None,
    commit_sha: str | None,
    relative_path: str,
) -> tuple[str, str | None]:
    validated_commit_sha = None
    if commit_sha is not None:
        c_sha = commit_sha.strip()
        if not re.match(r"^[0-9a-fA-F]{40}$", c_sha):
            raise HTTPException(
                status_code=400,
                detail="commit_sha must be a full 40-character hexadecimal git commit SHA",
            )
        validated_commit_sha = c_sha.lower()

    if source is not None:
        src = source.strip()
        pattern = r"^https:\/\/github\.com\/([A-Za-z0-9_\-]+)\/([A-Za-z0-9_\-]+)\/blob\/([0-9a-fA-F]{40})\/(.+)$"
        match = re.match(pattern, src)
        if not match:
            raise HTTPException(
                status_code=400,
                detail="source must be a valid GitHub blob URL format (https://github.com/{owner}/{repo}/blob/{commit_sha}/{path})",
            )
        owner, repo, url_sha, url_path = match.groups()
        if owner.lower() != "chungjinwoo":
            raise HTTPException(
                status_code=400,
                detail=f"source URL owner '{owner}' is not authorized",
            )
        if repo.lower() not in ALLOWED_GITHUB_REPOS:
            raise HTTPException(
                status_code=400,
                detail=f"source repository '{repo}' is not authorized",
            )

        url_sha_clean = url_sha.lower()
        if validated_commit_sha is not None and validated_commit_sha != url_sha_clean:
            raise HTTPException(
                status_code=400,
                detail="commit_sha does not match commit SHA in source URL",
            )
        validated_commit_sha = url_sha_clean

        norm_url_path = posixpath.normpath(urllib.parse.unquote(url_path).replace("\\", "/"))
        if norm_url_path != relative_path:
            raise HTTPException(
                status_code=400,
                detail=f"source URL path '{norm_url_path}' does not match relative_path '{relative_path}'",
            )
        return src, validated_commit_sha

    # If source is None:
    if validated_commit_sha is not None:
        constructed_source = f"https://github.com/CHUNGJINWOO/learning-portfolio/blob/{validated_commit_sha}/{relative_path}"
        return constructed_source, validated_commit_sha

    return "upload", None


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
def create_document(document: DocumentCreate, request: Request):
    try:
        context = get_request_authorization_context(request)
        scope = ProjectScope(document.project_id) if document.project_id is not None else GlobalScope()
        authorize_write(context, scope)

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

    except AuthorizationDenied as e:
        raise HTTPException(status_code=403, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
def list_documents(request: Request, project_id: int | None = None):
    try:
        context = get_request_authorization_context(request)
        if project_id is not None:
            authorize_read(context, ProjectScope(project_id))
        else:
            if not (context.can_read_global() or Capability.PROJECT_READ_ALL in context.capabilities):
                raise AuthorizationDenied(
                    "listing documents without project_id requires PROJECT_READ_ALL or RESOURCE_READ_GLOBAL"
                )

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
                        COUNT(c.id) AS chunk_count,
                        d.relative_path,
                        d.sha256
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
                        d.updated_at,
                        d.relative_path,
                        d.sha256
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
                        COUNT(c.id) AS chunk_count,
                        d.relative_path,
                        d.sha256
                    FROM documents d
                    LEFT JOIN document_chunks c
                        ON c.document_id = d.id
                    WHERE (
                        (d.project_id = ANY(%s))
                        OR (%s AND d.project_id IS NOT NULL)
                        OR (%s AND d.project_id IS NULL)
                    )
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
                        d.updated_at,
                        d.relative_path,
                        d.sha256
                    ORDER BY d.id;
                    """,
                    (
                        list(context.read_project_ids),
                        Capability.PROJECT_READ_ALL in context.capabilities,
                        context.can_read_global(),
                    ),
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
                    "relative_path": row[11],
                    "sha256": row[12],
                }
                for row in rows
            ],
        }

    except HTTPException:
        raise

    except AuthorizationDenied as e:
        raise HTTPException(status_code=403, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------
# Document semantic search
# -------------------------

@router.post("/upload")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    project_id: int | None = None,
    project_id_form: int | None = Form(default=None, alias="project_id"),
    expected_sha256: str | None = Form(default=None),
    title: str | None = Form(default=None),
    relative_path: str | None = Form(default=None),
    source: str | None = Form(default=None),
    commit_sha: str | None = Form(default=None),
):
    try:
        if project_id is not None and project_id_form is not None and project_id != project_id_form:
            raise HTTPException(
                status_code=400,
                detail=f"Query parameter project_id ({project_id}) and form data project_id ({project_id_form}) do not match",
            )
        effective_project_id = project_id if project_id is not None else project_id_form
        context = get_request_authorization_context(request)
        scope = ProjectScope(effective_project_id) if effective_project_id is not None else GlobalScope()
        authorize_write(context, scope)

        if not file.filename:
            raise HTTPException(
                status_code=400,
                detail="Filename is required",
            )

        safe_name = Path(file.filename).name

        has_provenance = relative_path is not None
        if has_provenance:
            _validate_safe_filename(safe_name, relative_path)
            stored_relative_path = _validate_and_normalize_path(relative_path, safe_name)
            stored_source, stored_commit_sha = _validate_source_and_commit(
                source, commit_sha, stored_relative_path
            )
        else:
            # Legacy upload: preserve original behavior, no provenance constraints or deduplication
            stored_relative_path = safe_name
            stored_source = source if source is not None else "upload"
            stored_commit_sha = commit_sha

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

        raw_bytes = await file.read()

        if not raw_bytes:
            raise HTTPException(
                status_code=400,
                detail="Uploaded file is empty",
            )

        if len(raw_bytes) > MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Uploaded file size ({len(raw_bytes)} bytes) exceeds limit of {MAX_UPLOAD_SIZE_BYTES} bytes",
            )

        sha256 = hashlib.sha256(raw_bytes).hexdigest()

        if expected_sha256 is not None:
            expected_clean = expected_sha256.strip().lower()
            if expected_clean != sha256:
                raise HTTPException(
                    status_code=400,
                    detail=f"Uploaded file SHA-256 '{sha256}' does not match expected SHA-256 '{expected_clean}'",
                )

        with get_db_connection() as conn:
            if effective_project_id is not None:
                project = conn.execute(
                    """
                    SELECT id
                    FROM projects
                    WHERE id = %s;
                    """,
                    (effective_project_id,),
                ).fetchone()

                if project is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Project not found",
                    )

            if has_provenance:
                # Transaction-level PostgreSQL Advisory Lock for serialization
                # on canonical key (effective_project_id, stored_relative_path)
                lock_hash = hashlib.sha256(
                    f"{effective_project_id}:{stored_relative_path}".encode("utf-8")
                ).hexdigest()[:15]
                lock_key = int(lock_hash, 16) & 0x7FFFFFFFFFFFFFFF
                conn.execute("SELECT pg_advisory_xact_lock(%s);", (lock_key,))

                # Query existing active document matching canonical identity key: (project_id, relative_path)
                if effective_project_id is not None:
                    existing_doc = conn.execute(
                        """
                        SELECT
                            d.id,
                            d.project_id,
                            d.title,
                            d.filename,
                            d.relative_path,
                            d.sha256,
                            d.file_size,
                            d.source,
                            d.description,
                            COUNT(c.id) AS chunk_count
                        FROM documents d
                        LEFT JOIN document_chunks c ON c.document_id = d.id
                        WHERE d.project_id = %s
                          AND d.relative_path = %s
                          AND d.status = 'active'
                        GROUP BY
                            d.id, d.project_id, d.title, d.filename,
                            d.relative_path, d.sha256, d.file_size,
                            d.source, d.description
                        ORDER BY d.id DESC
                        LIMIT 1;
                        """,
                        (effective_project_id, stored_relative_path),
                    ).fetchone()
                else:
                    existing_doc = conn.execute(
                        """
                        SELECT
                            d.id,
                            d.project_id,
                            d.title,
                            d.filename,
                            d.relative_path,
                            d.sha256,
                            d.file_size,
                            d.source,
                            d.description,
                            COUNT(c.id) AS chunk_count
                        FROM documents d
                        LEFT JOIN document_chunks c ON c.document_id = d.id
                        WHERE d.project_id IS NULL
                          AND d.relative_path = %s
                          AND d.status = 'active'
                        GROUP BY
                            d.id, d.project_id, d.title, d.filename,
                            d.relative_path, d.sha256, d.file_size,
                            d.source, d.description
                        ORDER BY d.id DESC
                        LIMIT 1;
                        """,
                        (stored_relative_path,),
                    ).fetchone()

                if existing_doc is not None:
                    (
                        existing_id,
                        existing_proj_id,
                        existing_title,
                        existing_filename,
                        existing_rel_path,
                        existing_sha256,
                        existing_size,
                        existing_source,
                        existing_desc,
                        existing_chunk_count,
                    ) = existing_doc

                    # Case A: Same project, same path, same SHA-256 -> Duplicate / Unchanged
                    if existing_sha256 == sha256:
                        existing_commit_sha = None
                        existing_assertion = None
                        if existing_desc:
                            try:
                                parsed_desc = json.loads(existing_desc)
                                if isinstance(parsed_desc, dict):
                                    existing_commit_sha = parsed_desc.get("commit_sha")
                                    existing_assertion = parsed_desc.get("provenance_assertion")
                            except Exception:
                                pass

                        integrity_status = "client_sha256_match" if expected_sha256 is not None else "unverified_sha256"
                        return {
                            "status": "duplicate",
                            "document_id": existing_id,
                            "project_id": existing_proj_id,
                            "filename": existing_filename,
                            "relative_path": existing_rel_path or stored_relative_path,
                            "source": existing_source,
                            "commit_sha": existing_commit_sha,
                            "upload_integrity": integrity_status,
                            "provenance_assertion": existing_assertion or ("client_git_commit" if existing_commit_sha else None),
                            "server_git_verified": False,
                            "file_size": existing_size,
                            "sha256": existing_sha256,
                            "chunk_count": existing_chunk_count,
                            "message": "Document already exists with identical content; no changes made",
                        }

                    # Case B: Same project, same path, different SHA-256 -> Conflict / Update required
                    else:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                f"Document already exists at '{stored_relative_path}' with different content "
                                f"(SHA-256 mismatch: existing {existing_sha256[:8] if existing_sha256 else 'none'}, "
                                f"incoming {sha256[:8]}). Automatic replacement is disabled."
                            ),
                        )

            # Not duplicate or conflict (or legacy upload): proceed to write file and ingest
            upload_root = Path(os.environ.get("DOCUMENTS_UPLOAD_DIR", "/data/documents"))
            upload_root.mkdir(parents=True, exist_ok=True)

            # Write to a request-unique temporary file to isolate parsing and avoid overwriting/unlinking committed files
            temp_file_name = f"tmp_{uuid.uuid4().hex}_{safe_name}"
            temp_path = upload_root / temp_file_name
            temp_path.write_bytes(raw_bytes)

            try:
                pages = extract_text(
                    temp_path,
                    file.content_type,
                )
                chunks = chunk_pages(pages)
                if not chunks:
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

                # Promote to final content-addressed immutable path
                target_name = f"{sha256}_{safe_name}"
                target_path = upload_root / target_name
                if target_path.exists():
                    if target_path.stat().st_size != len(raw_bytes):
                        temp_path.replace(target_path)
                    else:
                        temp_path.unlink(missing_ok=True)
                else:
                    temp_path.replace(target_path)

                document_title = title or Path(file.filename).stem

                meta_dict = {}
                if stored_commit_sha:
                    meta_dict["commit_sha"] = stored_commit_sha
                    meta_dict["provenance_assertion"] = "client_git_commit"
                if stored_relative_path != safe_name:
                    meta_dict["relative_path"] = stored_relative_path
                if stored_source != "upload":
                    meta_dict["source"] = stored_source
                if expected_sha256 is not None:
                    meta_dict["upload_integrity"] = "client_sha256_match"
                else:
                    meta_dict["upload_integrity"] = "unverified_sha256"
                meta_dict["server_git_verified"] = False

                stored_description = json.dumps(meta_dict, ensure_ascii=False) if meta_dict else None

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
                        effective_project_id,
                        document_title,
                        safe_name,
                        file.content_type,
                        stored_source,
                        stored_description,
                        str(target_path),
                        len(raw_bytes),
                        sha256,
                        suffix.lstrip("."),
                        stored_relative_path,
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
                    "project_id": effective_project_id,
                    "filename": safe_name,
                    "relative_path": stored_relative_path,
                    "source": stored_source,
                    "commit_sha": stored_commit_sha,
                    "upload_integrity": "client_sha256_match" if expected_sha256 is not None else "unverified_sha256",
                    "provenance_assertion": "client_git_commit" if stored_commit_sha else None,
                    "server_git_verified": False,
                    "file_size": len(raw_bytes),
                    "sha256": sha256,
                    "chunk_count": len(chunks),
                }

            except ValueError as e:
                temp_path.unlink(missing_ok=True)
                raise HTTPException(status_code=400, detail=str(e))

            except Exception:
                temp_path.unlink(missing_ok=True)
                raise

    except HTTPException:
        raise

    except AuthorizationDenied as e:
        raise HTTPException(status_code=403, detail=str(e))

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )


@router.get("/search")
def search_documents(
    request: Request,
    q: str,
    limit: int = 5,
    project_id: int | None = None,
):
    try:
        context = get_request_authorization_context(request)
        if project_id is not None:
            authorize_read(context, ProjectScope(project_id))
        else:
            if not (context.can_read_global() or Capability.PROJECT_READ_ALL in context.capabilities):
                raise AuthorizationDenied(
                    "searching documents without project_id requires PROJECT_READ_ALL or RESOURCE_READ_GLOBAL"
                )

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

    except AuthorizationDenied as e:
        raise HTTPException(status_code=403, detail=str(e))

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )


@router.patch("/{document_id}")
def update_document(
    document_id: int,
    document: DocumentUpdate,
    request: Request,
):
    try:
        context = get_request_authorization_context(request)
        if not (context.write_project_ids or context.can_write_global()):
            raise AuthorizationDenied(
                f"identity is not authorized for durable writes: {context.identity}"
            )

        with get_db_connection() as conn:
            if context.can_write_global():
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
                    WHERE id = %s
                      AND (
                          project_id = ANY(%s)
                          OR project_id IS NULL
                      );
                    """,
                    (document_id, list(context.write_project_ids)),
                ).fetchone()
            else:
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
                    WHERE id = %s
                      AND project_id = ANY(%s);
                    """,
                    (document_id, list(context.write_project_ids)),
                ).fetchone()

            if current is None:
                raise HTTPException(
                    status_code=404,
                    detail="Document not found",
                )

            source_project_id = current[0]
            scope = ProjectScope(source_project_id) if source_project_id is not None else GlobalScope()
            authorize_write(context, scope)

            fields_set = (
                document.model_fields_set
                if hasattr(document, "model_fields_set")
                else document.__fields_set__
            )

            if "project_id" in fields_set and document.project_id != source_project_id:
                if source_project_id is not None and document.project_id is not None:
                    raise AuthorizationDenied(
                        "reassigning resource ownership between projects is not permitted"
                    )

                if not context.can_write_global():
                    raise AuthorizationDenied(
                        "changing global resource ownership requires RESOURCE_WRITE_GLOBAL capability"
                    )

                if document.project_id is not None:
                    authorize_write(context, ProjectScope(document.project_id))

            project_id = (
                document.project_id
                if "project_id" in fields_set
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

    except AuthorizationDenied as e:
        raise HTTPException(status_code=403, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{document_id}")
def get_document(document_id: int, request: Request):
    try:
        context = get_request_authorization_context(request)

        with get_db_connection() as conn:
            if Capability.PROJECT_READ_ALL in context.capabilities:
                row = conn.execute(
                    """
                    SELECT project_id
                    FROM documents
                    WHERE id = %s
                      AND (
                          project_id IS NOT NULL
                          OR (%s AND project_id IS NULL)
                      );
                    """,
                    (document_id, context.can_read_global()),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT project_id
                    FROM documents
                    WHERE id = %s
                      AND (
                          project_id = ANY(%s)
                          OR (%s AND project_id IS NULL)
                      );
                    """,
                    (
                        document_id,
                        list(context.read_project_ids),
                        context.can_read_global(),
                    ),
                ).fetchone()

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Document not found",
                )

            source_project_id = row[0]
            scope = ProjectScope(source_project_id) if source_project_id is not None else GlobalScope()
            authorize_read(context, scope)

        result = fetch_document(document_id)

        if result is None:
            raise HTTPException(
                status_code=404,
                detail="Document not found",
            )

        return result

    except HTTPException:
        raise

    except AuthorizationDenied as e:
        raise HTTPException(status_code=403, detail=str(e))

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )


@router.delete("/{document_id}")
def delete_document(document_id: int, request: Request):
    try:
        context = get_request_authorization_context(request)
        if not (context.write_project_ids or context.can_write_global()):
            raise AuthorizationDenied(
                f"identity is not authorized for durable writes: {context.identity}"
            )

        with get_db_connection() as conn:
            if context.can_write_global():
                row = conn.execute(
                    """
                    SELECT project_id, title
                    FROM documents
                    WHERE id = %s
                      AND (
                          project_id = ANY(%s)
                          OR project_id IS NULL
                      );
                    """,
                    (document_id, list(context.write_project_ids)),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT project_id, title
                    FROM documents
                    WHERE id = %s
                      AND project_id = ANY(%s);
                    """,
                    (document_id, list(context.write_project_ids)),
                ).fetchone()

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Document not found",
                )

            source_project_id = row[0]
            scope = ProjectScope(source_project_id) if source_project_id is not None else GlobalScope()
            authorize_write(context, scope)

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

    except AuthorizationDenied as e:
        raise HTTPException(status_code=403, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{document_id}/chunks")
def create_document_chunk(
    document_id: int,
    chunk: DocumentChunkCreate,
    request: Request,
):
    try:
        context = get_request_authorization_context(request)
        if not (context.write_project_ids or context.can_write_global()):
            raise AuthorizationDenied(
                f"identity is not authorized for durable writes: {context.identity}"
            )

        with get_db_connection() as conn:
            if context.can_write_global():
                document = conn.execute(
                    """
                    SELECT id, project_id
                    FROM documents
                    WHERE id = %s
                      AND (
                          project_id = ANY(%s)
                          OR project_id IS NULL
                      );
                    """,
                    (document_id, list(context.write_project_ids)),
                ).fetchone()
            else:
                document = conn.execute(
                    """
                    SELECT id, project_id
                    FROM documents
                    WHERE id = %s
                      AND project_id = ANY(%s);
                    """,
                    (document_id, list(context.write_project_ids)),
                ).fetchone()

            if document is None:
                raise HTTPException(
                    status_code=404,
                    detail="Document not found",
                )

            parent_project_id = document[1]
            scope = ProjectScope(parent_project_id) if parent_project_id is not None else GlobalScope()
            authorize_write(context, scope)

            embedding = Vector(
                model.encode(
                    "passage: " + chunk.content,
                    normalize_embeddings=True,
                ).tolist()
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

    except AuthorizationDenied as e:
        raise HTTPException(status_code=403, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{document_id}/chunks")
def list_document_chunks(document_id: int, request: Request):
    try:
        context = get_request_authorization_context(request)

        with get_db_connection() as conn:
            if Capability.PROJECT_READ_ALL in context.capabilities:
                document = conn.execute(
                    """
                    SELECT id, project_id
                    FROM documents
                    WHERE id = %s
                      AND (
                          project_id IS NOT NULL
                          OR (%s AND project_id IS NULL)
                      );
                    """,
                    (document_id, context.can_read_global()),
                ).fetchone()
            else:
                document = conn.execute(
                    """
                    SELECT id, project_id
                    FROM documents
                    WHERE id = %s
                      AND (
                          project_id = ANY(%s)
                          OR (%s AND project_id IS NULL)
                      );
                    """,
                    (
                        document_id,
                        list(context.read_project_ids),
                        context.can_read_global(),
                    ),
                ).fetchone()

            if document is None:
                raise HTTPException(
                    status_code=404,
                    detail="Document not found",
                )

            parent_project_id = document[1]
            scope = ProjectScope(parent_project_id) if parent_project_id is not None else GlobalScope()
            authorize_read(context, scope)

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

    except AuthorizationDenied as e:
        raise HTTPException(status_code=403, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
