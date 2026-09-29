"""Transform unified search results into a provenance-aware AI context."""

from typing import Any, Literal

from pydantic import BaseModel

from app.core.search import search_context


class ContextSource(BaseModel):
    """A retrievable source record suitable for citations."""

    source_id: str
    kind: Literal["memory", "document"]
    project_id: int | None = None
    memory_id: int | None = None
    document_id: int | None = None
    chunk_id: int | None = None
    filename: str | None = None
    relative_path: str | None = None
    chunk_index: int | None = None
    page_number: int | None = None
    source: str | None = None
    distance: float | None = None
    hybrid_score: float | None = None


class ContextItem(BaseModel):
    """Normalized content item with an explicit link to its provenance."""

    item_id: str
    kind: Literal["memory", "document"]
    content: str
    source_id: str
    metadata: dict[str, Any]


class AssembledContext(BaseModel):
    query: str
    project_id: int | None = None
    memory_count: int
    document_count: int
    memories: list[ContextItem]
    documents: list[ContextItem]
    sources: list[ContextSource]


def assemble_context(
    query: str,
    limit: int = 5,
    project_id: int | None = None,
) -> dict[str, Any]:
    """Search once and normalize results while retaining actual provenance."""
    result = search_context(query=query, limit=limit, project_id=project_id)
    memories: list[ContextItem] = []
    documents: list[ContextItem] = []
    sources: list[ContextSource] = []

    for raw in result["memories"]:
        memory_id = raw["id"]
        source_id = f"memory:{memory_id}"
        sources.append(
            ContextSource(
                source_id=source_id,
                kind="memory",
                project_id=raw.get("project_id"),
                memory_id=memory_id,
                source=raw.get("source"),
                distance=raw.get("distance"),
            )
        )
        memories.append(
            ContextItem(
                item_id=source_id,
                kind="memory",
                content=raw["content"],
                source_id=source_id,
                metadata={
                    key: raw.get(key)
                    for key in ("memory_type", "category", "importance")
                    if key in raw
                },
            )
        )

    for raw in result["documents"]:
        chunk_id = raw["chunk_id"]
        source_id = f"document-chunk:{chunk_id}"
        sources.append(
            ContextSource(
                source_id=source_id,
                kind="document",
                project_id=raw.get("project_id"),
                document_id=raw.get("document_id"),
                chunk_id=chunk_id,
                filename=raw.get("filename"),
                relative_path=raw.get("relative_path"),
                chunk_index=raw.get("chunk_index"),
                page_number=raw.get("page_number"),
                source=raw.get("source"),
                distance=raw.get("distance"),
                hybrid_score=raw.get("hybrid_score"),
            )
        )
        documents.append(
            ContextItem(
                item_id=source_id,
                kind="document",
                content=raw["content"],
                source_id=source_id,
                metadata={
                    key: raw.get(key)
                    for key in ("title", "document_type")
                    if key in raw
                },
            )
        )

    return AssembledContext(
        query=result["query"],
        project_id=result.get("project_id"),
        memory_count=len(memories),
        document_count=len(documents),
        memories=memories,
        documents=documents,
        sources=sources,
    ).model_dump()
