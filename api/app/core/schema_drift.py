"""Structured, read-only comparison of canonical schema representations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.core.schema_fingerprint import (
    IdentityDiagnostic,
    compare_schema,
)
from app.core.schema_representation import canonicalize_schema


_CATEGORIES = (
    "extensions",
    "tables",
    "columns",
    "sequences",
    "constraints",
    "indexes",
)
_NAMED_CATEGORIES = {"constraints", "indexes", "sequences"}


@dataclass(frozen=True)
class CategoryDrift:
    added: tuple[dict[str, Any], ...] = ()
    removed: tuple[dict[str, Any], ...] = ()
    changed: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class SchemaDriftReport:
    semantic_match: bool
    repository_identity_match: bool
    eligible_without_reconciliation: bool
    categories: Mapping[str, CategoryDrift]
    name_mismatches: tuple[IdentityDiagnostic, ...]


def compare_schema_drift(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
) -> SchemaDriftReport:
    """Return added, removed, changed, and name-only drift diagnostics."""
    expected_schema = canonicalize_schema(expected)
    actual_schema = canonicalize_schema(actual)
    comparison = compare_schema(expected_schema, actual_schema)
    categories = {
        category: _compare_category(
            category,
            expected_schema.get(category, []),
            actual_schema.get(category, []),
        )
        for category in _CATEGORIES
    }
    return SchemaDriftReport(
        semantic_match=comparison.semantic_match,
        repository_identity_match=not comparison.name_mismatches,
        eligible_without_reconciliation=comparison.eligible_without_reconciliation,
        categories=categories,
        name_mismatches=comparison.name_mismatches,
    )


def _compare_category(
    category: str,
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
) -> CategoryDrift:
    expected_by_identity = _index_by_key(category, expected, semantic=False)
    actual_by_identity = _index_by_key(category, actual, semantic=False)
    changed: list[dict[str, Any]] = []

    for key in sorted(set(expected_by_identity) & set(actual_by_identity), key=str):
        if expected_by_identity[key] != actual_by_identity[key]:
            changed.append({
                "identity": key,
                "expected": expected_by_identity[key],
                "actual": actual_by_identity[key],
            })

    expected_semantic = _index_by_key(category, expected, semantic=True)
    actual_semantic = _index_by_key(category, actual, semantic=True)
    return CategoryDrift(
        added=tuple(
            actual_semantic[key]
            for key in sorted(set(actual_semantic) - set(expected_semantic), key=str)
        ),
        removed=tuple(
            expected_semantic[key]
            for key in sorted(set(expected_semantic) - set(actual_semantic), key=str)
        ),
        changed=tuple(changed),
    )


def _index_by_key(
    category: str,
    items: list[dict[str, Any]],
    *,
    semantic: bool,
) -> dict[tuple[Any, ...], dict[str, Any]]:
    result: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in items:
        if semantic and category in _NAMED_CATEGORIES:
            values = {
                key: _freeze(value)
                for key, value in item.items()
                if key != "name"
            }
        else:
            values = _identity_values(category, item)
        key = tuple(sorted(values.items()))
        if key in result:
            raise ValueError(f"duplicate {category} identity: {key}")
        result[key] = item
    return result


def _identity_values(category: str, item: dict[str, Any]) -> dict[str, Any]:
    """Return stable fields that identify one repository object."""
    fields_by_category = {
        "extensions": ("schema", "name"),
        "tables": ("schema", "name"),
        "columns": ("schema", "table", "name"),
        "sequences": ("schema", "name"),
        "constraints": ("schema", "table", "name"),
        "indexes": ("schema", "table", "name"),
    }
    return {
        field: _freeze(item.get(field))
        for field in fields_by_category[category]
    }


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple((key, _freeze(item)) for key, item in sorted(value.items()))
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value
