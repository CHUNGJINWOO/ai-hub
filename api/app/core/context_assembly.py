"""Transform unified search results into a provenance-aware AI context."""

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, model_validator

from app.core.search import search_context


ContextKind = Literal["memory", "document"]


class ContextSource(BaseModel):
    """A retrievable source record suitable for citations."""

    source_id: str
    kind: ContextKind
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
    kind: ContextKind
    content: str
    source_id: str
    metadata: dict[str, Any]


class CanonicalContext(BaseModel):
    """Transport-neutral context with uniform items and source references."""

    context_schema_version: Literal["1"] = "1"
    query: str
    project_id: int | None = None
    memory_count: int
    document_count: int
    items: list[ContextItem]
    sources: list[ContextSource]

    @model_validator(mode="after")
    def validate_source_references(self) -> "CanonicalContext":
        sources_by_id = {source.source_id: source for source in self.sources}
        if len(sources_by_id) != len(self.sources):
            raise ValueError("source_id values must be unique")

        item_ids = [item.item_id for item in self.items]
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("item_id values must be unique")

        for item in self.items:
            source = sources_by_id.get(item.source_id)
            if source is None:
                raise ValueError(
                    f"missing source for context item: {item.source_id}"
                )
            if source.kind != item.kind:
                raise ValueError(
                    f"context item and source kinds differ: {item.source_id}"
                )

        if {item.source_id for item in self.items} != set(sources_by_id):
            raise ValueError("every context source must be linked to an item")

        if self.memory_count != sum(item.kind == "memory" for item in self.items):
            raise ValueError("memory_count does not match context items")
        if self.document_count != sum(
            item.kind == "document" for item in self.items
        ):
            raise ValueError("document_count does not match context items")

        return self


class AssembledContext(BaseModel):
    """Legacy REST response shape retained for existing consumers."""

    query: str
    project_id: int | None = None
    memory_count: int
    document_count: int
    memories: list[ContextItem]
    documents: list[ContextItem]
    sources: list[ContextSource]


def _validate_search_result(result: Any) -> None:
    if not isinstance(result, Mapping):
        raise ValueError("search_context must return a mapping")

    for key in ("query", "memories", "documents"):
        if key not in result:
            raise ValueError(f"search_context result is missing: {key}")

    for key in ("memories", "documents"):
        values = result[key]
        if not isinstance(values, list):
            raise ValueError(
                f"search_context result field must be a list: {key}"
            )
        if not all(isinstance(value, Mapping) for value in values):
            raise ValueError(
                f"search_context result items must be mappings: {key}"
            )

        required_keys = (
            ("id", "content")
            if key == "memories"
            else ("chunk_id", "content")
        )
        for value in values:
            missing = [item for item in required_keys if item not in value]
            if missing:
                raise ValueError(
                    f"search_context result item is missing "
                    f"{', '.join(missing)}: {key}"
                )


def assemble_canonical_context(
    query: str,
    limit: int = 5,
    project_id: int | None = None,
) -> CanonicalContext:
    """Search once and normalize results into the canonical context model."""
    result = search_context(query=query, limit=limit, project_id=project_id)
    _validate_search_result(result)
    items: list[ContextItem] = []
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
        items.append(
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
        items.append(
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

    return CanonicalContext(
        context_schema_version="1",
        query=result["query"],
        project_id=result.get("project_id"),
        memory_count=sum(item.kind == "memory" for item in items),
        document_count=sum(item.kind == "document" for item in items),
        items=items,
        sources=sources,
    )


def assemble_context(
    query: str,
    limit: int = 5,
    project_id: int | None = None,
) -> dict[str, Any]:
    """Adapt canonical context to the existing REST response shape."""
    context = assemble_canonical_context(
        query=query,
        limit=limit,
        project_id=project_id,
    )
    memories = [item for item in context.items if item.kind == "memory"]
    documents = [item for item in context.items if item.kind == "document"]

    return AssembledContext(
        query=context.query,
        project_id=context.project_id,
        memory_count=context.memory_count,
        document_count=context.document_count,
        memories=memories,
        documents=documents,
        sources=context.sources,
    ).model_dump()
