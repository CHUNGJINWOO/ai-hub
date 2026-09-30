import unittest

from app.core.schema_drift import compare_schema_drift


def fixture():
    return {
        "tables": [{"schema": "public", "name": "projects"}],
        "columns": [
            {
                "schema": "public",
                "table": "projects",
                "name": "id",
                "ordinal_position": 1,
                "type": "bigint",
                "nullable": False,
                "default": None,
            }
        ],
        "constraints": [
            {
                "schema": "public",
                "table": "projects",
                "name": "projects_pkey",
                "constraint_type": "PRIMARY KEY",
                "columns": ["id"],
            }
        ],
        "indexes": [
            {
                "schema": "public",
                "table": "projects",
                "name": "projects_pkey",
                "access_method": "btree",
                "columns": ["id"],
                "unique": True,
                "primary": True,
                "predicate": None,
                "expressions": [],
            }
        ],
    }


class SchemaDriftTests(unittest.TestCase):
    def test_no_drift(self):
        report = compare_schema_drift(fixture(), fixture())
        self.assertTrue(report.semantic_match)
        self.assertTrue(report.repository_identity_match)
        self.assertTrue(report.eligible_without_reconciliation)
        self.assertEqual(report.categories["tables"].added, ())
        self.assertEqual(report.categories["tables"].removed, ())
        self.assertEqual(report.categories["tables"].changed, ())

    def test_added_and_removed_objects(self):
        expected = fixture()
        actual = fixture()
        actual["tables"].append({"schema": "public", "name": "memories"})
        actual["columns"].pop()
        report = compare_schema_drift(expected, actual)
        self.assertFalse(report.semantic_match)
        self.assertEqual(report.categories["tables"].added[0]["name"], "memories")
        self.assertEqual(report.categories["columns"].removed[0]["name"], "id")

    def test_changed_semantic_payload(self):
        expected = fixture()
        actual = fixture()
        actual["indexes"][0]["access_method"] = "hash"
        report = compare_schema_drift(expected, actual)
        self.assertFalse(report.semantic_match)
        self.assertEqual(len(report.categories["indexes"].changed), 1)

    def test_name_only_mismatch_is_not_semantic_drift(self):
        expected = fixture()
        actual = fixture()
        actual["indexes"][0]["name"] = "projects_pkey_v2"
        report = compare_schema_drift(expected, actual)
        self.assertTrue(report.semantic_match)
        self.assertFalse(report.repository_identity_match)
        self.assertFalse(report.eligible_without_reconciliation)
        self.assertEqual(report.categories["indexes"].added, ())
        self.assertEqual(report.categories["indexes"].removed, ())
        self.assertEqual(len(report.name_mismatches), 1)

    def test_input_order_does_not_change_report(self):
        expected = fixture()
        actual = fixture()
        actual["columns"].reverse()
        actual["constraints"].reverse()
        first = compare_schema_drift(expected, actual)
        second = compare_schema_drift(actual, expected)
        self.assertEqual(first.semantic_match, second.semantic_match)
        self.assertEqual(first.categories, second.categories)


if __name__ == "__main__":
    unittest.main()
