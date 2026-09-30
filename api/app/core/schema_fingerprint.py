"""Semantic schema fingerprints and repository identity diagnostics."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from app.core.schema_representation import canonicalize_schema, serialize_schema


@dataclass(frozen=True)
class IdentityDiagnostic:
    category: str
    semantic_key: tuple[Any, ...]
    expected_name: str | None
    actual_name: str | None


@dataclass(frozen=True)
class SchemaComparison:
    expected_fingerprint: str
    actual_fingerprint: str
    semantic_match: bool
    name_mismatches: tuple[IdentityDiagnostic, ...]

    @property
    def eligible_without_reconciliation(self) -> bool:
        return self.semantic_match and not self.name_mismatches


_NAMED_CATEGORIES = ("constraints", "indexes", "sequences")


def semantic_representation(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Remove repository-only object names while retaining schema semantics."""
    result = canonicalize_schema(schema)
    for category in _NAMED_CATEGORIES:
        items = result.get(category, [])
        for item in items:
            item.pop("name", None)
        result[category] = sorted(items, key=_semantic_sort_key)
    return result


def schema_fingerprint(schema: Mapping[str, Any]) -> str:
    """Calculate SHA-256 for the semantic canonical representation."""
    return hashlib.sha256(serialize_schema(semantic_representation(schema))).hexdigest()


def compare_schema(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
) -> SchemaComparison:
    """Compare semantic schema identity and repository object names."""
    expected_canonical = canonicalize_schema(expected)
    actual_canonical = canonicalize_schema(actual)
    expected_fingerprint = schema_fingerprint(expected_canonical)
    actual_fingerprint = schema_fingerprint(actual_canonical)
    mismatches: list[IdentityDiagnostic] = []

    for category in _NAMED_CATEGORIES:
        expected_items = expected_canonical.get(category, [])
        actual_items = actual_canonical.get(category, [])
        expected_by_semantics = _group_without_name(expected_items)
        actual_by_semantics = _group_without_name(actual_items)
        for key in sorted(set(expected_by_semantics) & set(actual_by_semantics), key=str):
            expected_names = expected_by_semantics[key]
            actual_names = actual_by_semantics[key]
            if expected_names != actual_names:
                mismatches.append(
                    IdentityDiagnostic(
                        category=category,
                        semantic_key=key,
                        expected_name=expected_names[0] if expected_names else None,
                        actual_name=actual_names[0] if actual_names else None,
                    )
                )

    return SchemaComparison(
        expected_fingerprint=expected_fingerprint,
        actual_fingerprint=actual_fingerprint,
        semantic_match=expected_fingerprint == actual_fingerprint,
        name_mismatches=tuple(mismatches),
    )


def _group_without_name(items: list[dict[str, Any]]) -> dict[tuple[Any, ...], list[str]]:
    groups: dict[tuple[Any, ...], list[str]] = {}
    for item in items:
        name = item.get("name")
        semantic = tuple(
            (key, _freeze(value))
            for key, value in sorted(item.items())
            if key != "name"
        )
        groups.setdefault(semantic, []).append(name)
    for names in groups.values():
        names.sort()
    return groups


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple((key, _freeze(item)) for key, item in sorted(value.items()))
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _semantic_sort_key(item: dict[str, Any]) -> str:
    """Sort named objects after removing their repository-only names."""
    return json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
