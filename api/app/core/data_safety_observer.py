"""Read-only PostgreSQL observations for the DB-free data-safety layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from app.core.data_safety import (
    ObservedDataSafety,
    SequenceObservation,
)
from app.core.catalog_queries import READ_ONLY_BEGIN, READ_ONLY_ROLLBACK


class ObservationConnection(Protocol):
    def execute(self, query: str) -> Any:
        ...


@dataclass(frozen=True)
class ForeignKeyObservation:
    identifier: str
    child_table: str
    child_column: str
    parent_table: str
    parent_column: str


@dataclass(frozen=True)
class DataSafetyObservationTargets:
    tables: tuple[str, ...] = ()
    required_columns: tuple[tuple[str, str], ...] = ()
    relationships: tuple[ForeignKeyObservation, ...] = ()
    sequences: tuple[str, ...] = ()


class PostgresDataSafetyObserver:
    """Collect facts without applying any safety policy or changing data."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        targets: DataSafetyObservationTargets,
        *,
        schema: str = "public",
    ) -> None:
        _validate_identifier(schema)
        self._connection_factory = connection_factory
        self._targets = targets
        self._schema = schema

    def observe(self) -> ObservedDataSafety:
        with self._connection_factory() as connection:
            connection.execute(READ_ONLY_BEGIN)
            try:
                database, schema = _identity(connection, self._schema)
                row_counts = {
                    table: _scalar(
                        connection,
                        _count_query(self._schema, table),
                    )
                    for table in self._targets.tables
                }
                null_counts = {
                    (table, column): _scalar(
                        connection,
                        _null_count_query(self._schema, table, column),
                    )
                    for table, column in self._targets.required_columns
                }
                orphan_counts = {
                    relation.identifier: _scalar(
                        connection,
                        _orphan_query(self._schema, relation),
                    )
                    for relation in self._targets.relationships
                }
                sequence_states = {
                    sequence: _sequence_observation(
                        connection,
                        self._schema,
                        sequence,
                    )
                    for sequence in self._targets.sequences
                }
            finally:
                connection.execute(READ_ONLY_ROLLBACK)

        return ObservedDataSafety(
            database=database,
            schema=schema,
            row_counts=row_counts,
            null_counts=null_counts,
            orphan_counts=orphan_counts,
            sequence_states=sequence_states,
            backup_confirmed=None,
            restore_rehearsal_confirmed=None,
        )


def _identity(
    connection: ObservationConnection,
    schema: str,
) -> tuple[str | None, str | None]:
    row = _one(
        connection,
        "SELECT current_database() AS database_name, "
        "nspname AS schema_name "
        "FROM pg_namespace WHERE nspname = "
        f"{_literal(schema)}",
    )
    database = row.get("database_name")
    actual_schema = row.get("schema_name")
    return (
        database if isinstance(database, str) else None,
        actual_schema if isinstance(actual_schema, str) else None,
    )


def _sequence_observation(
    connection: ObservationConnection,
    schema: str,
    sequence: str,
) -> SequenceObservation:
    row = _one(
        connection,
        "SELECT last_value, "
        "format('%s.%s', owner_ns.nspname, owner_table.relname) "
        "|| '.' || owner_attr.attname AS owner "
        f"FROM {_qualified(schema, sequence)} AS sequence "
        "LEFT JOIN pg_depend AS dependency "
        f"ON dependency.objid = {_qualified_literal(schema, sequence)}::regclass "
        "AND dependency.deptype = 'a' "
        "LEFT JOIN pg_class AS owner_table "
        "ON owner_table.oid = dependency.refobjid "
        "LEFT JOIN pg_namespace AS owner_ns "
        "ON owner_ns.oid = owner_table.relnamespace "
        "LEFT JOIN pg_attribute AS owner_attr "
        "ON owner_attr.attrelid = dependency.refobjid "
        "AND owner_attr.attnum = dependency.refobjsubid",
    )
    value = row.get("last_value")
    return SequenceObservation(
        current_value=value if isinstance(value, int) else None,
        owner=row.get("owner") if isinstance(row.get("owner"), str) else None,
    )


def _count_query(schema: str, table: str) -> str:
    return f"SELECT count(*) AS value FROM {_qualified(schema, table)}"


def _null_count_query(schema: str, table: str, column: str) -> str:
    return (
        f"SELECT count(*) AS value FROM {_qualified(schema, table)} "
        f"WHERE {_identifier(column)} IS NULL"
    )


def _orphan_query(schema: str, relation: ForeignKeyObservation) -> str:
    child = _qualified(schema, relation.child_table)
    parent = _qualified(schema, relation.parent_table)
    child_column = _identifier(relation.child_column)
    parent_column = _identifier(relation.parent_column)
    return (
        "SELECT count(*) AS value "
        f"FROM {child} AS child "
        f"LEFT JOIN {parent} AS parent "
        f"ON parent.{parent_column} = child.{child_column} "
        f"WHERE child.{child_column} IS NOT NULL "
        f"AND parent.{parent_column} IS NULL"
    )


def _qualified(schema: str, name: str) -> str:
    _validate_identifier(schema)
    _validate_identifier(name)
    return f"{_identifier(schema)}.{_identifier(name)}"


def _identifier(value: str) -> str:
    _validate_identifier(value)
    return f'"{value}"'


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _qualified_literal(schema: str, name: str) -> str:
    _validate_identifier(schema)
    _validate_identifier(name)
    return _literal(f'"{schema}"."{name}"')


def _validate_identifier(value: str) -> None:
    if not value or "\x00" in value or '"' in value or ";" in value:
        raise ValueError(f"invalid SQL identifier: {value!r}")


def _one(connection: ObservationConnection, query: str) -> dict[str, Any]:
    row = connection.execute(query).fetchone()
    return dict(row) if row is not None else {}


def _scalar(connection: ObservationConnection, query: str) -> int | None:
    row = _one(connection, query)
    value = row.get("value")
    return value if isinstance(value, int) and value >= 0 else None
