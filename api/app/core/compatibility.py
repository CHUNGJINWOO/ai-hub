"""Pure compatibility policy checks for migration validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.schema_catalog import CatalogSnapshot


@dataclass(frozen=True)
class CompatibilityPolicy:
    """Supported versions must be explicitly supplied by the operator."""

    postgres_majors: frozenset[int]
    vector_versions: frozenset[str]
    expected_extension_schema: str | None = None
    expected_vector_dimension: int | None = None


@dataclass(frozen=True)
class CompatibilityCheckResult:
    check_name: str
    expected: Any
    actual: Any
    passed: bool
    reason: str
    failure_code: str | None = None

    @property
    def component(self) -> str:
        return self.check_name

    @property
    def observed(self) -> str:
        return "" if self.actual is None else str(self.actual)

    @property
    def accepted(self) -> bool:
        return self.passed


@dataclass(frozen=True)
class ObservedDatabaseCompatibility:
    """Compatibility facts supplied by a catalog snapshot or operator."""

    postgres_major: int | None
    pgvector_version: str | None
    extension_name: str | None
    extension_schema: str | None
    vector_dimension: int | None

    @classmethod
    def from_snapshot(
        cls,
        snapshot: CatalogSnapshot,
        *,
        postgres_major: int | None = None,
    ) -> "ObservedDatabaseCompatibility":
        extensions = [
            row for row in snapshot.rows.get("extensions", [])
            if row.get("name") == "vector"
        ]
        extension = extensions[0] if len(extensions) == 1 else None
        dimension = _vector_dimension(snapshot.rows.get("columns", []))
        return cls(
            postgres_major=postgres_major,
            pgvector_version=_as_string(extension, "version"),
            extension_name=_as_string(extension, "name"),
            extension_schema=_as_string(extension, "schema"),
            vector_dimension=dimension,
        )


@dataclass(frozen=True)
class CompatibilityResult:
    compatible: bool
    postgres_result: CompatibilityCheckResult
    pgvector_result: CompatibilityCheckResult
    extension_schema_result: CompatibilityCheckResult
    vector_dimension_result: CompatibilityCheckResult
    failures: tuple[CompatibilityCheckResult, ...]


def check_postgres_major(
    major: int | None,
    policy: CompatibilityPolicy,
) -> CompatibilityCheckResult:
    if major is None:
        return CompatibilityCheckResult(
            check_name="postgres_major",
            expected=sorted(policy.postgres_majors),
            actual=None,
            passed=False,
            reason="PostgreSQL major is unavailable",
            failure_code="UNKNOWN_POSTGRES_MAJOR",
        )
    accepted = major in policy.postgres_majors
    return CompatibilityCheckResult(
        check_name="postgres_major",
        expected=sorted(policy.postgres_majors),
        actual=major,
        passed=accepted,
        reason=(
            "PostgreSQL major is explicitly supported"
            if accepted
            else "PostgreSQL major is not explicitly supported"
        ),
        failure_code=None if accepted else "UNSUPPORTED_POSTGRES_MAJOR",
    )


def check_vector_version(
    version: str | None,
    policy: CompatibilityPolicy,
) -> CompatibilityCheckResult:
    if version is None:
        return CompatibilityCheckResult(
            check_name="pgvector_version",
            expected=sorted(policy.vector_versions),
            actual=None,
            passed=False,
            reason="pgvector version is unavailable",
            failure_code="UNKNOWN_PGVECTOR_VERSION",
        )
    accepted = version in policy.vector_versions
    return CompatibilityCheckResult(
        check_name="pgvector_version",
        expected=sorted(policy.vector_versions),
        actual=version,
        passed=accepted,
        reason=(
            "pgvector version is explicitly supported"
            if accepted
            else "pgvector version is not explicitly supported"
        ),
        failure_code=None if accepted else "UNSUPPORTED_PGVECTOR_VERSION",
    )


def check_extension_schema(
    observed: ObservedDatabaseCompatibility,
    policy: CompatibilityPolicy,
) -> CompatibilityCheckResult:
    expected = policy.expected_extension_schema
    if observed.extension_name != "vector":
        return CompatibilityCheckResult(
            check_name="extension_schema",
            expected=expected,
            actual=observed.extension_schema,
            passed=False,
            reason="required vector extension is unavailable",
            failure_code="EXTENSION_NOT_FOUND",
        )
    if expected is None:
        return CompatibilityCheckResult(
            check_name="extension_schema",
            expected=None,
            actual=observed.extension_schema,
            passed=True,
            reason="extension schema policy is not configured",
        )
    if observed.extension_schema is None:
        return CompatibilityCheckResult(
            check_name="extension_schema",
            expected=expected,
            actual=None,
            passed=False,
            reason="vector extension schema is unavailable",
            failure_code="EXTENSION_SCHEMA_MISMATCH",
        )
    passed = observed.extension_schema == expected
    return CompatibilityCheckResult(
        check_name="extension_schema",
        expected=expected,
        actual=observed.extension_schema,
        passed=passed,
        reason="vector extension schema matches policy" if passed else "vector extension schema does not match policy",
        failure_code=None if passed else "EXTENSION_SCHEMA_MISMATCH",
    )


def check_vector_dimension(
    observed: ObservedDatabaseCompatibility,
    policy: CompatibilityPolicy,
) -> CompatibilityCheckResult:
    expected = policy.expected_vector_dimension
    actual = observed.vector_dimension
    if expected is None:
        return CompatibilityCheckResult(
            check_name="vector_dimension",
            expected=None,
            actual=actual,
            passed=True,
            reason="vector dimension policy is not configured",
        )
    if actual is None:
        return CompatibilityCheckResult(
            check_name="vector_dimension",
            expected=expected,
            actual=None,
            passed=False,
            reason="vector dimension is unavailable",
            failure_code="VECTOR_DIMENSION_UNKNOWN",
        )
    passed = actual == expected
    return CompatibilityCheckResult(
        check_name="vector_dimension",
        expected=expected,
        actual=actual,
        passed=passed,
        reason="vector dimension matches policy" if passed else "vector dimension does not match policy",
        failure_code=None if passed else "VECTOR_DIMENSION_MISMATCH",
    )


def inspect_compatibility(
    observed: ObservedDatabaseCompatibility,
    policy: CompatibilityPolicy,
) -> CompatibilityResult:
    checks = (
        check_postgres_major(observed.postgres_major, policy),
        check_vector_version(observed.pgvector_version, policy),
        check_extension_schema(observed, policy),
        check_vector_dimension(observed, policy),
    )
    failures = tuple(check for check in checks if not check.passed)
    return CompatibilityResult(
        compatible=not failures,
        postgres_result=checks[0],
        pgvector_result=checks[1],
        extension_schema_result=checks[2],
        vector_dimension_result=checks[3],
        failures=failures,
    )


def _as_string(row: dict[str, Any] | None, key: str) -> str | None:
    value = row.get(key) if row is not None else None
    return value if isinstance(value, str) else None


def _vector_dimension(rows: list[dict[str, Any]]) -> int | None:
    dimensions: set[int] = set()
    for row in rows:
        type_name = row.get("type")
        if not isinstance(type_name, str) or not type_name.startswith("vector("):
            continue
        value = type_name.removeprefix("vector(").removesuffix(")")
        if value.isdigit():
            dimensions.add(int(value))
    return dimensions.pop() if len(dimensions) == 1 else None
