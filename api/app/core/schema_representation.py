"""Canonical, database-free representation of a PostgreSQL schema."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


_WHITESPACE = re.compile(r"\s+")


def normalize_expression(expression: str | None) -> str | None:
    """Normalize catalog expression formatting without rewriting SQL semantics."""
    if expression is None:
        return None
    return _WHITESPACE.sub(" ", expression.strip())


def normalize_schema(value: Any) -> Any:
    """Return a recursively normalized and deterministically ordered value."""
    if isinstance(value, Mapping):
        normalized = {
            str(key): normalize_schema(value[key])
            for key in sorted(value, key=str)
        }
        for key in (
            "default",
            "check_expression",
            "expression",
            "expressions",
            "predicate",
            "definition",
            "canonical_definition",
        ):
            if key in normalized:
                normalized[key] = _normalize_expression_value(normalized[key])
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [normalize_schema(item) for item in value]
    return value


def _normalize_expression_value(value: Any) -> Any:
    if isinstance(value, str):
        return normalize_expression(value)
    if isinstance(value, list):
        return [_normalize_expression_value(item) for item in value]
    return value


def canonicalize_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize schema categories and sort their records by contract keys."""
    result = normalize_schema(schema)
    if not isinstance(result, dict):
        raise TypeError("schema representation must be a mapping")

    ordering = {
        "extensions": ("schema", "name"),
        "tables": ("schema", "name"),
        "columns": ("schema", "table", "ordinal_position"),
        "sequences": ("schema", "name"),
        "constraints": ("schema", "table", "constraint_type", "name"),
        "indexes": ("schema", "table", "name"),
    }
    for category, keys in ordering.items():
        values = result.get(category)
        if values is not None:
            if not isinstance(values, list) or not all(
                isinstance(item, dict) for item in values
            ):
                raise TypeError(f"{category} must be a list of objects")
            result[category] = sorted(
                values,
                key=lambda item: tuple(
                    (item.get(key) is None, item.get(key)) for key in keys
                ),
            )
    return result


def serialize_schema(schema: Mapping[str, Any]) -> bytes:
    """Serialize a canonical schema as compact UTF-8 JSON without a newline."""
    canonical = canonicalize_schema(schema)
    return json.dumps(
        canonical,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
