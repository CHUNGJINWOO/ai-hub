"""Read-only PostgreSQL catalog snapshots and canonical row conversion."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable, Mapping
from typing import Any, Callable, Protocol

from app.core.catalog_queries import (
    CATALOG_QUERIES,
    READ_ONLY_BEGIN,
    READ_ONLY_ROLLBACK,
)

CATALOG_CATEGORIES = (
    "extensions",
    "tables",
    "columns",
    "sequences",
    "constraints",
    "indexes",
)


class CatalogConnection(Protocol):
    def execute(self, query: str) -> Any:
        ...


@dataclass(frozen=True)
class CatalogSnapshot:
    database: str
    schema: str
    rows: dict[str, list[dict[str, Any]]]

    def as_schema_rows(self) -> dict[str, list[dict[str, Any]]]:
        return catalog_rows_to_schema(self.rows)


def catalog_rows_to_schema(
    rows: Mapping[str, Iterable[Mapping[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Validate and copy raw catalog rows into canonical category buckets."""
    unknown = set(rows) - set(CATALOG_CATEGORIES)
    if unknown:
        raise ValueError(f"unknown catalog categories: {sorted(unknown)}")
    return {
        category: [dict(row) for row in rows.get(category, ())]
        for category in CATALOG_CATEGORIES
    }


class PostgresCatalogReader:
    """Read a PostgreSQL 17 catalog using a read-only transaction.

    The reader owns neither application startup nor schema mutation. The
    connection factory is injected so callers and tests control connection
    creation explicitly.
    """

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def read_snapshot(self) -> CatalogSnapshot:
        with self._connection_factory() as connection:
            connection.execute(READ_ONLY_BEGIN)
            try:
                identity = _fetch_rows(connection, "identity")
                rows = {
                    category: _fetch_rows(connection, category)
                    for category in CATALOG_CATEGORIES
                }
            finally:
                connection.execute(READ_ONLY_ROLLBACK)

        if len(identity) != 1:
            raise ValueError("catalog identity query must return exactly one row")
        database = identity[0].get("database_name")
        schema = identity[0].get("schema_name")
        if not isinstance(database, str) or not isinstance(schema, str):
            raise ValueError("catalog identity row has invalid values")
        return CatalogSnapshot(database=database, schema=schema, rows=rows)


def _fetch_rows(connection: CatalogConnection, category: str) -> list[dict[str, Any]]:
    cursor = connection.execute(CATALOG_QUERIES[category])
    rows = cursor.fetchall()
    return [dict(row) for row in rows]
