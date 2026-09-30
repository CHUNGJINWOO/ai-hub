import unittest

from app.core.catalog_queries import (
    CATALOG_QUERIES,
    READ_ONLY_BEGIN,
    READ_ONLY_ROLLBACK,
)
from app.core.schema_catalog import (
    CATALOG_CATEGORIES,
    PostgresCatalogReader,
    catalog_rows_to_schema,
)
from app.core.schema_fingerprint import schema_fingerprint


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query):
        self.executed.append(query)
        if query == READ_ONLY_BEGIN or query == READ_ONLY_ROLLBACK:
            return FakeCursor([])
        if "current_database()" in query:
            return FakeCursor([{
                "database_name": "aihub",
                "schema_name": "public",
            }])
        for category, category_query in CATALOG_QUERIES.items():
            if query == category_query:
                return FakeCursor(self.rows.get(category, []))
        raise AssertionError(f"unexpected query: {query}")


class SchemaCatalogTests(unittest.TestCase):
    def test_rows_are_copied_into_all_canonical_categories(self):
        rows = catalog_rows_to_schema({"tables": [{"schema": "public", "name": "projects"}]})
        self.assertEqual(set(rows), set(CATALOG_CATEGORIES))
        self.assertEqual(rows["tables"][0]["name"], "projects")
        self.assertEqual(rows["indexes"], [])

    def test_unknown_category_is_rejected(self):
        with self.assertRaises(ValueError):
            catalog_rows_to_schema({"unknown": []})

    def test_reader_returns_snapshot_and_rolls_back(self):
        connection = FakeConnection({
            "extensions": [{"schema": "public", "name": "vector", "version": "0.8.6"}],
            "tables": [{"schema": "public", "name": "projects", "relation_kind": "r"}],
            "columns": [{"schema": "public", "table": "projects", "name": "id"}],
            "sequences": [],
            "constraints": [],
            "indexes": [],
        })
        snapshot = PostgresCatalogReader(lambda: connection).read_snapshot()
        self.assertEqual(snapshot.database, "aihub")
        self.assertEqual(snapshot.schema, "public")
        self.assertEqual(snapshot.rows["extensions"][0]["version"], "0.8.6")
        self.assertEqual(connection.executed[0], READ_ONLY_BEGIN)
        self.assertEqual(connection.executed[-1], READ_ONLY_ROLLBACK)

    def test_snapshot_rows_feed_existing_fingerprint(self):
        rows = {
            "tables": [{"schema": "public", "name": "projects"}],
            "columns": [],
            "constraints": [],
            "indexes": [],
            "sequences": [],
            "extensions": [{"schema": "public", "name": "vector"}],
        }
        snapshot = PostgresCatalogReader(
            lambda: FakeConnection({
                "extensions": rows["extensions"],
                "tables": rows["tables"],
                "columns": rows["columns"],
                "sequences": rows["sequences"],
                "constraints": rows["constraints"],
                "indexes": rows["indexes"],
            })
        ).read_snapshot()
        self.assertEqual(schema_fingerprint(snapshot.as_schema_rows()),
                         schema_fingerprint(rows))

    def test_catalog_queries_are_select_only(self):
        for query in CATALOG_QUERIES.values():
            self.assertTrue(query.lstrip().upper().startswith("SELECT"))
            self.assertNotRegex(query.upper(), r"\b(CREATE|ALTER|DROP|INSERT|UPDATE|DELETE)\b")


if __name__ == "__main__":
    unittest.main()
