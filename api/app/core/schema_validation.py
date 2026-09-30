"""Validation-only aggregation for migration foundation results."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.compatibility import CompatibilityResult
from app.core.data_safety import DataSafetyResult
from app.core.schema_drift import SchemaDriftReport

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SchemaValidationReport:
    semantic_fingerprint: str
    drift: SchemaDriftReport
    compatibility: CompatibilityResult
    data_safety: DataSafetyResult
    status: str
    evidence_source: str = "db_free"

    @property
    def valid(self) -> bool:
        return self.status == PASS


def build_schema_validation_report(
    *,
    semantic_fingerprint: str,
    drift: SchemaDriftReport,
    compatibility: CompatibilityResult,
    data_safety: DataSafetyResult,
    evidence_source: str = "db_free",
) -> SchemaValidationReport:
    """Aggregate existing results without observing or mutating a database."""
    if evidence_source not in {"db_free", "fixture", "controlled_postgresql"}:
        raise ValueError(f"unsupported evidence source: {evidence_source}")
    status = _overall_status(drift, compatibility, data_safety)
    return SchemaValidationReport(
        semantic_fingerprint=semantic_fingerprint,
        drift=drift,
        compatibility=compatibility,
        data_safety=data_safety,
        status=status,
        evidence_source=evidence_source,
    )


def _overall_status(
    drift: SchemaDriftReport,
    compatibility: CompatibilityResult,
    data_safety: DataSafetyResult,
) -> str:
    if (
        not drift.semantic_match
        or not drift.repository_identity_match
        or not compatibility.compatible
        and any(
            failure.failure_code
            and not failure.failure_code.startswith("UNKNOWN_")
            and failure.failure_code not in {"VECTOR_DIMENSION_UNKNOWN"}
            for failure in compatibility.failures
        )
        or not data_safety.safe
        and data_safety.failures
    ):
        return FAIL
    if (
        not drift.eligible_without_reconciliation
        or compatibility.failures
        or data_safety.unknowns
    ):
        return UNKNOWN
    return PASS
