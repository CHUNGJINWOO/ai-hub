"""DB-free data-safety policy checks for migration validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"
NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class SequencePolicy:
    minimum: int | None = None
    maximum: int | None = None
    expected_owner: str | None = None


@dataclass(frozen=True)
class SequenceObservation:
    current_value: int | None
    owner: str | None = None


@dataclass(frozen=True)
class DataSafetyPolicy:
    expected_database: str | None = None
    expected_schema: str | None = None
    expected_row_counts: Mapping[str, int] = field(default_factory=dict)
    max_null_counts: Mapping[tuple[str, str], int] = field(default_factory=dict)
    max_orphan_counts: Mapping[str, int] = field(default_factory=dict)
    sequence_policies: Mapping[str, SequencePolicy] = field(default_factory=dict)
    require_backup_confirmation: bool = False
    require_restore_rehearsal: bool = False


@dataclass(frozen=True)
class ObservedDataSafety:
    database: str | None = None
    schema: str | None = None
    row_counts: Mapping[str, int] = field(default_factory=dict)
    null_counts: Mapping[tuple[str, str], int] = field(default_factory=dict)
    orphan_counts: Mapping[str, int] = field(default_factory=dict)
    sequence_states: Mapping[str, SequenceObservation] = field(default_factory=dict)
    backup_confirmed: bool | None = None
    backup_evidence: str | None = None
    restore_rehearsal_confirmed: bool | None = None
    restore_evidence: str | None = None


@dataclass(frozen=True)
class DataSafetyCheckResult:
    check_name: str
    status: str
    passed: bool
    expected: Any
    observed: Any
    reason: str
    failure_code: str | None = None


@dataclass(frozen=True)
class DataSafetyResult:
    safe: bool
    checks: tuple[DataSafetyCheckResult, ...]
    failures: tuple[DataSafetyCheckResult, ...]
    unknowns: tuple[DataSafetyCheckResult, ...]


def evaluate_data_safety(
    policy: DataSafetyPolicy,
    observed: ObservedDataSafety,
) -> DataSafetyResult:
    _validate_observations(observed)
    checks = [
        *_identity_checks(policy, observed),
        *_row_count_checks(policy, observed),
        *_null_checks(policy, observed),
        *_orphan_checks(policy, observed),
        *_sequence_checks(policy, observed),
        _evidence_check(
            "backup_confirmation",
            policy.require_backup_confirmation,
            observed.backup_confirmed,
            observed.backup_evidence,
        ),
        _evidence_check(
            "restore_rehearsal",
            policy.require_restore_rehearsal,
            observed.restore_rehearsal_confirmed,
            observed.restore_evidence,
        ),
    ]
    checks.sort(key=lambda check: check.check_name)
    failures = tuple(check for check in checks if check.status == FAIL)
    unknowns = tuple(check for check in checks if check.status == UNKNOWN)
    return DataSafetyResult(
        safe=not failures and not unknowns,
        checks=tuple(checks),
        failures=failures,
        unknowns=unknowns,
    )


def _identity_checks(
    policy: DataSafetyPolicy,
    observed: ObservedDataSafety,
) -> list[DataSafetyCheckResult]:
    checks = []
    if policy.expected_database is not None:
        checks.append(
            _value_check(
                "database_identity",
                policy.expected_database,
                observed.database,
                "database identity matches",
                "database identity mismatch",
                "DATABASE_IDENTITY_MISMATCH",
                unknown_code="DATABASE_IDENTITY_UNKNOWN",
            )
        )
    if policy.expected_schema is not None:
        checks.append(
            _value_check(
                "schema_identity",
                policy.expected_schema,
                observed.schema,
                "schema identity matches",
                "schema identity mismatch",
                "SCHEMA_IDENTITY_MISMATCH",
                unknown_code="SCHEMA_IDENTITY_UNKNOWN",
            )
        )
    return checks


def _row_count_checks(
    policy: DataSafetyPolicy,
    observed: ObservedDataSafety,
) -> list[DataSafetyCheckResult]:
    return [
        _value_check(
            f"row_count:{table}",
            expected,
            observed.row_counts.get(table),
            "row count matches policy",
            "row count violates policy",
            "ROW_COUNT_MISMATCH",
            unknown_code="ROW_COUNT_UNKNOWN",
        )
        for table, expected in sorted(policy.expected_row_counts.items())
    ]


def _null_checks(
    policy: DataSafetyPolicy,
    observed: ObservedDataSafety,
) -> list[DataSafetyCheckResult]:
    return [
        _maximum_check(
            f"null_count:{table}.{column}",
            maximum,
            observed.null_counts.get((table, column)),
            "NULL count is within policy",
            "NULL count exceeds policy",
            "NULL_COUNT_EXCEEDED",
            "NULL_COUNT_UNKNOWN",
        )
        for (table, column), maximum in sorted(policy.max_null_counts.items())
    ]


def _orphan_checks(
    policy: DataSafetyPolicy,
    observed: ObservedDataSafety,
) -> list[DataSafetyCheckResult]:
    return [
        _maximum_check(
            f"orphan_count:{relationship}",
            maximum,
            observed.orphan_counts.get(relationship),
            "orphan count is within policy",
            "orphan count exceeds policy",
            "ORPHAN_COUNT_EXCEEDED",
            "ORPHAN_COUNT_UNKNOWN",
        )
        for relationship, maximum in sorted(policy.max_orphan_counts.items())
    ]


def _sequence_checks(
    policy: DataSafetyPolicy,
    observed: ObservedDataSafety,
) -> list[DataSafetyCheckResult]:
    checks = []
    for name, expected in sorted(policy.sequence_policies.items()):
        actual = observed.sequence_states.get(name)
        if actual is None:
            checks.append(_unknown(f"sequence:{name}", expected, None, "sequence state is unavailable", "SEQUENCE_UNKNOWN"))
            continue
        valid = (
            actual.current_value is not None
            and (expected.minimum is None or actual.current_value >= expected.minimum)
            and (expected.maximum is None or actual.current_value <= expected.maximum)
            and (expected.expected_owner is None or actual.owner == expected.expected_owner)
        )
        checks.append(
            DataSafetyCheckResult(
                check_name=f"sequence:{name}",
                status=PASS if valid else FAIL,
                passed=valid,
                expected=expected,
                observed=actual,
                reason="sequence state satisfies policy" if valid else "sequence state violates policy",
                failure_code=None if valid else "SEQUENCE_STATE_INVALID",
            )
        )
    return checks


def _evidence_check(
    name: str,
    required: bool,
    confirmed: bool | None,
    evidence: str | None,
) -> DataSafetyCheckResult:
    if not required:
        return _not_applicable(name, "evidence is not required")
    if confirmed is None:
        return _unknown(name, True, confirmed, "required evidence is unavailable", f"{name.upper()}_UNKNOWN")
    if confirmed:
        return DataSafetyCheckResult(name, PASS, True, True, confirmed, "required evidence is confirmed")
    return DataSafetyCheckResult(name, FAIL, False, True, confirmed, "required evidence was explicitly rejected", f"{name.upper()}_FAILED")


def _value_check(
    name: str,
    expected: Any,
    actual: Any,
    pass_reason: str,
    fail_reason: str,
    failure_code: str,
    *,
    unknown_code: str,
) -> DataSafetyCheckResult:
    if actual is None:
        return _unknown(name, expected, actual, "required observation is unavailable", unknown_code)
    passed = actual == expected
    return DataSafetyCheckResult(name, PASS if passed else FAIL, passed, expected, actual, pass_reason if passed else fail_reason, None if passed else failure_code)


def _maximum_check(
    name: str,
    maximum: int,
    actual: int | None,
    pass_reason: str,
    fail_reason: str,
    failure_code: str,
    unknown_code: str,
) -> DataSafetyCheckResult:
    if actual is None:
        return _unknown(name, maximum, actual, "required observation is unavailable", unknown_code)
    passed = actual <= maximum
    return DataSafetyCheckResult(name, PASS if passed else FAIL, passed, maximum, actual, pass_reason if passed else fail_reason, None if passed else failure_code)


def _unknown(name: str, expected: Any, actual: Any, reason: str, code: str) -> DataSafetyCheckResult:
    return DataSafetyCheckResult(name, UNKNOWN, False, expected, actual, reason, code)


def _not_applicable(name: str, reason: str) -> DataSafetyCheckResult:
    return DataSafetyCheckResult(name, NOT_APPLICABLE, True, None, None, reason)


def _validate_observations(observed: ObservedDataSafety) -> None:
    count_maps = (
        observed.row_counts,
        observed.null_counts,
        observed.orphan_counts,
    )
    for values in count_maps:
        if any(not isinstance(value, int) or value < 0 for value in values.values()):
            raise ValueError("observed safety counts must be non-negative integers")
    for state in observed.sequence_states.values():
        if state.current_value is not None and not isinstance(state.current_value, int):
            raise ValueError("sequence current values must be integers or None")
