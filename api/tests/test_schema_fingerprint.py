import unittest

from app.core.compatibility import (
    CompatibilityPolicy,
    check_postgres_major,
    check_vector_version,
)
from app.core.schema_catalog import catalog_rows_to_schema
from app.core.schema_fingerprint import (
    compare_schema,
    schema_fingerprint,
    semantic_representation,
)
from app.core.schema_representation import serialize_schema


def schema_fixture() -> dict:
    return {
        "tables": [{"schema": "public", "name": "projects"}],
        "columns": [
            {
                "schema": "public",
                "table": "projects",
                "ordinal_position": 1,
                "name": "id",
                "type": "bigint",
                "nullable": False,
                "default": " nextval('projects_id_seq'::regclass) ",
            }
        ],
        "constraints": [
            {
                "schema": "public",
                "table": "projects",
                "constraint_type": "PRIMARY KEY",
                "name": "projects_pkey",
                "columns": ["id"],
            }
        ],
        "indexes": [
            {
                "schema": "public",
                "table": "projects",
                "name": "projects_pkey",
                "unique": True,
                "access_method": "btree",
                "columns": ["id"],
                "predicate": None,
                "expressions": [],
            }
        ],
    }


def multi_named_fixture() -> dict:
    return {
        "constraints": [
            {
                "schema": "public",
                "table": "projects",
                "constraint_type": "CHECK",
                "name": "projects_status_check",
                "check_expression": "status IN ('active', 'archived')",
            },
            {
                "schema": "public",
                "table": "projects",
                "constraint_type": "CHECK",
                "name": "projects_name_check",
                "check_expression": "length(name) > 0",
            },
        ],
        "indexes": [
            {
                "schema": "public",
                "table": "projects",
                "name": "idx_projects_status",
                "access_method": "btree",
                "columns": ["status"],
                "predicate": None,
            },
            {
                "schema": "public",
                "table": "projects",
                "name": "idx_projects_name",
                "access_method": "btree",
                "columns": ["name"],
                "predicate": None,
            },
        ],
        "sequences": [
            {
                "schema": "public",
                "name": "projects_id_seq",
                "data_type": "bigint",
                "increment": 1,
                "cache": 1,
                "cycle": False,
            }
        ],
    }


class SchemaFingerprintTests(unittest.TestCase):
    def test_serialization_is_stable_for_mapping_and_list_order(self):
        first = {
            "tables": [{"schema": "public", "name": "b"}, {"schema": "public", "name": "a"}],
            "extensions": [{"name": "vector", "schema": "public"}],
        }
        second = {
            "extensions": [{"schema": "public", "name": "vector"}],
            "tables": [{"name": "a", "schema": "public"}, {"name": "b", "schema": "public"}],
        }
        self.assertEqual(serialize_schema(first), serialize_schema(second))

    def test_semantic_changes_change_fingerprint(self):
        original = schema_fixture()
        changed = schema_fixture()
        changed["columns"][0]["type"] = "text"
        self.assertNotEqual(schema_fingerprint(original), schema_fingerprint(changed))

    def test_nullability_and_default_changes_change_fingerprint(self):
        original = schema_fixture()
        nullable = schema_fixture()
        nullable["columns"][0]["nullable"] = True
        default = schema_fixture()
        default["columns"][0]["default"] = "nextval('other'::regclass)"
        self.assertNotEqual(schema_fingerprint(original), schema_fingerprint(nullable))
        self.assertNotEqual(schema_fingerprint(original), schema_fingerprint(default))

    def test_constraint_and_index_semantics_change_fingerprint(self):
        original = schema_fixture()
        fk_target = schema_fixture()
        fk_target["constraints"][0]["referenced_table"] = "other"
        access_method = schema_fixture()
        access_method["indexes"][0]["access_method"] = "hash"
        predicate = schema_fixture()
        predicate["indexes"][0]["predicate"] = "id > 0"
        expression = schema_fixture()
        expression["indexes"][0]["expressions"] = ["lower(id)"]
        for changed in (fk_target, access_method, predicate, expression):
            self.assertNotEqual(schema_fingerprint(original), schema_fingerprint(changed))

    def test_name_only_index_change_is_repository_identity_drift(self):
        expected = schema_fixture()
        actual = schema_fixture()
        actual["indexes"][0]["name"] = "projects_pkey_v2"
        comparison = compare_schema(expected, actual)
        self.assertTrue(comparison.semantic_match)
        self.assertFalse(comparison.eligible_without_reconciliation)
        self.assertEqual(comparison.name_mismatches[0].category, "indexes")

    def test_multiple_constraint_name_only_renames_are_diagnostic_only(self):
        expected = multi_named_fixture()
        actual = multi_named_fixture()
        actual["constraints"][0]["name"] = "projects_name_check_v2"
        actual["constraints"][1]["name"] = "projects_status_check_v2"
        comparison = compare_schema(expected, actual)
        self.assertTrue(comparison.semantic_match)
        self.assertEqual(len(comparison.name_mismatches), 2)
        self.assertFalse(comparison.eligible_without_reconciliation)

    def test_multiple_index_name_only_renames_are_diagnostic_only(self):
        expected = multi_named_fixture()
        actual = multi_named_fixture()
        actual["indexes"][0]["name"] = "idx_projects_name_v2"
        actual["indexes"][1]["name"] = "idx_projects_status_v2"
        comparison = compare_schema(expected, actual)
        self.assertTrue(comparison.semantic_match)
        self.assertEqual(len(comparison.name_mismatches), 2)
        self.assertFalse(comparison.eligible_without_reconciliation)

    def test_sequence_name_only_rename_is_repository_identity_drift(self):
        expected = multi_named_fixture()
        actual = multi_named_fixture()
        actual["sequences"][0]["name"] = "projects_id_seq_v2"
        comparison = compare_schema(expected, actual)
        self.assertTrue(comparison.semantic_match)
        self.assertEqual(comparison.name_mismatches[0].category, "sequences")

    def test_semantic_representation_is_stable_for_reordered_input(self):
        expected = multi_named_fixture()
        actual = multi_named_fixture()
        actual["constraints"].reverse()
        actual["indexes"].reverse()
        self.assertEqual(
            semantic_representation(expected),
            semantic_representation(actual),
        )
        self.assertEqual(schema_fingerprint(expected), schema_fingerprint(actual))

    def test_sequence_semantics_changes_change_fingerprint(self):
        original = multi_named_fixture()
        for field, value in (("increment", 2), ("cache", 2), ("cycle", True)):
            changed = multi_named_fixture()
            changed["sequences"][0][field] = value
            self.assertNotEqual(schema_fingerprint(original), schema_fingerprint(changed))

    def test_fk_action_change_changes_fingerprint(self):
        original = schema_fixture()
        changed = schema_fixture()
        changed["constraints"][0]["on_delete"] = "CASCADE"
        self.assertNotEqual(schema_fingerprint(original), schema_fingerprint(changed))

    def test_identifier_spelling_is_not_expression_normalization(self):
        first = {"tables": [{"schema": "public", "name": "a  b"}]}
        second = {"tables": [{"schema": "public", "name": "a b"}]}
        self.assertNotEqual(serialize_schema(first), serialize_schema(second))

    def test_catalog_categories_are_explicit(self):
        schema = catalog_rows_to_schema({"tables": [{"name": "projects"}]})
        self.assertEqual(set(schema), {
            "extensions", "tables", "columns", "sequences", "constraints", "indexes"
        })

    def test_compatibility_rejects_unlisted_versions(self):
        policy = CompatibilityPolicy(
            postgres_majors=frozenset({17}),
            vector_versions=frozenset({"0.8.6"}),
        )
        self.assertTrue(check_postgres_major(17, policy).accepted)
        self.assertFalse(check_postgres_major(18, policy).accepted)
        self.assertTrue(check_vector_version("0.8.6", policy).accepted)
        self.assertFalse(check_vector_version("unknown", policy).accepted)


if __name__ == "__main__":
    unittest.main()
