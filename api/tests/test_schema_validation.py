import unittest

from app.core.compatibility import (
    CompatibilityPolicy,
    ObservedDatabaseCompatibility,
    inspect_compatibility,
)
from app.core.data_safety import DataSafetyPolicy, ObservedDataSafety, evaluate_data_safety
from app.core.schema_drift import compare_schema_drift
from app.core.schema_fingerprint import schema_fingerprint
from app.core.schema_validation import (
    FAIL,
    PASS,
    UNKNOWN,
    build_schema_validation_report,
)


def schema():
    return {
        "extensions": [{"schema": "public", "name": "vector", "version": "0.8.6"}],
        "tables": [{"schema": "public", "name": "projects"}],
        "columns": [{"schema": "public", "table": "projects", "name": "embedding", "type": "vector(384)"}],
        "sequences": [],
        "constraints": [],
        "indexes": [{
            "schema": "public",
            "table": "projects",
            "name": "projects_embedding_idx",
            "access_method": "btree",
            "unique": False,
            "primary": False,
        }],
    }


def compatibility(observed=None):
    return inspect_compatibility(
        observed or ObservedDatabaseCompatibility(17, "0.8.6", "vector", "public", 384),
        CompatibilityPolicy(
            postgres_majors=frozenset({17}),
            vector_versions=frozenset({"0.8.6"}),
            expected_extension_schema="public",
            expected_vector_dimension=384,
        ),
    )


def safety(observed=None):
    return evaluate_data_safety(
        DataSafetyPolicy(expected_database="aihub"),
        observed or ObservedDataSafety(database="aihub"),
    )


class SchemaValidationTests(unittest.TestCase):
    def report(self, actual=None, observed_compatibility=None, observed_safety=None):
        expected = schema()
        actual = actual or expected
        return build_schema_validation_report(
            semantic_fingerprint=schema_fingerprint(actual),
            drift=compare_schema_drift(expected, actual),
            compatibility=compatibility(observed_compatibility),
            data_safety=safety(observed_safety),
        )

    def test_all_pass_is_pass(self):
        self.assertEqual(self.report().status, PASS)
        self.assertTrue(self.report().valid)

    def test_semantic_drift_is_fail(self):
        actual = schema()
        actual["columns"][0]["type"] = "vector(768)"
        self.assertEqual(self.report(actual).status, FAIL)

    def test_name_only_drift_is_unknown(self):
        actual = schema()
        actual["indexes"][0]["name"] = "projects_embedding_idx_v2"
        self.assertEqual(self.report(actual).status, FAIL)

    def test_unknown_compatibility_is_unknown(self):
        actual = ObservedDatabaseCompatibility(None, None, "vector", "public", None)
        self.assertEqual(self.report(observed_compatibility=actual).status, UNKNOWN)

    def test_safety_failure_is_fail_and_unknown_is_unknown(self):
        self.assertEqual(
            self.report(observed_safety=ObservedDataSafety(database="other")).status,
            FAIL,
        )
        self.assertEqual(
            self.report(observed_safety=ObservedDataSafety()).status,
            UNKNOWN,
        )

    def test_evidence_source_is_explicit_and_validated(self):
        report = build_schema_validation_report(
            semantic_fingerprint="fingerprint",
            drift=compare_schema_drift(schema(), schema()),
            compatibility=compatibility(),
            data_safety=safety(),
            evidence_source="fixture",
        )
        self.assertEqual(report.evidence_source, "fixture")
        with self.assertRaises(ValueError):
            build_schema_validation_report(
                semantic_fingerprint="fingerprint",
                drift=compare_schema_drift(schema(), schema()),
                compatibility=compatibility(),
                data_safety=safety(),
                evidence_source="production",
            )


if __name__ == "__main__":
    unittest.main()
