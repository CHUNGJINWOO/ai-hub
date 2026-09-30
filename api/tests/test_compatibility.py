import unittest

from app.core.compatibility import (
    CompatibilityPolicy,
    ObservedDatabaseCompatibility,
    check_extension_schema,
    check_postgres_major,
    check_vector_dimension,
    check_vector_version,
    inspect_compatibility,
)
from app.core.schema_catalog import CatalogSnapshot


def policy(**kwargs):
    values = {
        "postgres_majors": frozenset({17}),
        "vector_versions": frozenset({"0.8.6"}),
    }
    values.update(kwargs)
    return CompatibilityPolicy(**values)


def observed(**kwargs):
    values = {
        "postgres_major": 17,
        "pgvector_version": "0.8.6",
        "extension_name": "vector",
        "extension_schema": "public",
        "vector_dimension": 384,
    }
    values.update(kwargs)
    return ObservedDatabaseCompatibility(**values)


class CompatibilityTests(unittest.TestCase):
    def test_postgres_policy_is_explicit(self):
        self.assertTrue(check_postgres_major(17, policy()).passed)
        self.assertFalse(check_postgres_major(18, policy()).passed)
        self.assertEqual(
            check_postgres_major(None, policy()).failure_code,
            "UNKNOWN_POSTGRES_MAJOR",
        )
        self.assertTrue(
            check_postgres_major(
                18, policy(postgres_majors=frozenset({17, 18}))
            ).passed
        )

    def test_pgvector_policy_is_explicit(self):
        self.assertTrue(check_vector_version("0.8.6", policy()).passed)
        self.assertFalse(check_vector_version("0.8.7", policy()).passed)
        self.assertEqual(
            check_vector_version(None, policy()).failure_code,
            "UNKNOWN_PGVECTOR_VERSION",
        )
        self.assertTrue(
            check_vector_version(
                "0.8.7",
                policy(vector_versions=frozenset({"0.8.6", "0.8.7"})),
            ).passed
        )

    def test_extension_schema_requires_vector_and_matching_schema(self):
        self.assertTrue(
            check_extension_schema(
                observed(), policy(expected_extension_schema="public")
            ).passed
        )
        self.assertEqual(
            check_extension_schema(
                observed(extension_schema="extensions"),
                policy(expected_extension_schema="public"),
            ).failure_code,
            "EXTENSION_SCHEMA_MISMATCH",
        )
        self.assertEqual(
            check_extension_schema(
                observed(extension_name=None, extension_schema=None),
                policy(expected_extension_schema="public"),
            ).failure_code,
            "EXTENSION_NOT_FOUND",
        )
        self.assertTrue(
            check_extension_schema(observed(extension_schema=None), policy()).passed
        )

    def test_vector_dimension_policy_is_optional_but_strict_when_configured(self):
        self.assertTrue(
            check_vector_dimension(
                observed(vector_dimension=384),
                policy(expected_vector_dimension=384),
            ).passed
        )
        self.assertEqual(
            check_vector_dimension(
                observed(vector_dimension=768),
                policy(expected_vector_dimension=384),
            ).failure_code,
            "VECTOR_DIMENSION_MISMATCH",
        )
        self.assertEqual(
            check_vector_dimension(
                observed(vector_dimension=None),
                policy(expected_vector_dimension=384),
            ).failure_code,
            "VECTOR_DIMENSION_UNKNOWN",
        )
        self.assertTrue(
            check_vector_dimension(observed(vector_dimension=None), policy()).passed
        )

    def test_aggregate_preserves_all_failures(self):
        result = inspect_compatibility(
            observed(
                postgres_major=18,
                pgvector_version="0.8.7",
                extension_schema="extensions",
                vector_dimension=768,
            ),
            policy(expected_extension_schema="public", expected_vector_dimension=384),
        )
        self.assertFalse(result.compatible)
        self.assertEqual(
            [failure.failure_code for failure in result.failures],
            [
                "UNSUPPORTED_POSTGRES_MAJOR",
                "UNSUPPORTED_PGVECTOR_VERSION",
                "EXTENSION_SCHEMA_MISMATCH",
                "VECTOR_DIMENSION_MISMATCH",
            ],
        )

    def test_snapshot_observation_extracts_extension_and_dimension(self):
        snapshot = CatalogSnapshot(
            database="aihub",
            schema="public",
            rows={
                "extensions": [
                    {"name": "vector", "schema": "public", "version": "0.8.6"}
                ],
                "columns": [
                    {"type": "vector(384)"},
                    {"type": "text"},
                ],
            },
        )
        actual = ObservedDatabaseCompatibility.from_snapshot(
            snapshot, postgres_major=17
        )
        self.assertEqual(actual.pgvector_version, "0.8.6")
        self.assertEqual(actual.vector_dimension, 384)
        self.assertTrue(
            inspect_compatibility(
                actual,
                policy(expected_extension_schema="public", expected_vector_dimension=384),
            ).compatible
        )


if __name__ == "__main__":
    unittest.main()
