import unittest

from app.core.catalog_queries import READ_ONLY_BEGIN, READ_ONLY_ROLLBACK
from app.core.data_safety import (
    DataSafetyPolicy,
    ObservedDataSafety,
    evaluate_data_safety,
)
from app.core.data_safety_observer import (
    DataSafetyObservationTargets,
    ForeignKeyObservation,
    PostgresDataSafetyObserver,
)


class Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, available=True):
        self.available = available
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query):
        self.executed.append(query)
        if query == READ_ONLY_BEGIN or query == READ_ONLY_ROLLBACK:
            return Cursor(None)
        if "current_database()" in query:
            return Cursor({
                "database_name": "aihub",
                "schema_name": "public" if self.available else None,
            })
        if "last_value" in query:
            return Cursor({"last_value": 10, "owner": "public.projects.id"})
        if "LEFT JOIN" in query:
            return Cursor({"value": 0})
        if "count(*)" in query and "IS NULL" in query:
            return Cursor({"value": 1})
        if "count(*)" in query:
            return Cursor({"value": 3})
        raise AssertionError(f"unexpected query: {query}")


class DataSafetyObserverTests(unittest.TestCase):
    def targets(self):
        return DataSafetyObservationTargets(
            tables=("projects",),
            required_columns=(("projects", "name"),),
            relationships=(
                ForeignKeyObservation(
                    "documents.project_id->projects.id",
                    "documents",
                    "project_id",
                    "projects",
                    "id",
                ),
            ),
            sequences=("projects_id_seq",),
        )

    def test_read_only_transaction_and_observed_conversion(self):
        connection = FakeConnection()
        observed = PostgresDataSafetyObserver(
            lambda: connection,
            self.targets(),
        ).observe()
        self.assertEqual(connection.executed[0], READ_ONLY_BEGIN)
        self.assertEqual(connection.executed[-1], READ_ONLY_ROLLBACK)
        self.assertIsInstance(observed, ObservedDataSafety)
        self.assertEqual(observed.database, "aihub")
        self.assertEqual(observed.schema, "public")
        self.assertEqual(observed.row_counts["projects"], 3)
        self.assertEqual(observed.null_counts[("projects", "name")], 1)
        self.assertEqual(observed.orphan_counts["documents.project_id->projects.id"], 0)
        self.assertEqual(observed.sequence_states["projects_id_seq"].current_value, 10)

    def test_observer_queries_are_read_only(self):
        connection = FakeConnection()
        PostgresDataSafetyObserver(lambda: connection, self.targets()).observe()
        for query in connection.executed[1:-1]:
            self.assertTrue(query.lstrip().upper().startswith("SELECT"))
            self.assertNotRegex(
                query.upper(), r"\b(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE)\b"
            )

    def test_unknown_schema_propagates_to_data_safety(self):
        connection = FakeConnection(available=False)
        observed = PostgresDataSafetyObserver(
            lambda: connection,
            DataSafetyObservationTargets(tables=("projects",)),
        ).observe()
        result = evaluate_data_safety(
            DataSafetyPolicy(
                expected_database="aihub",
                expected_schema="public",
                expected_row_counts={"projects": 3},
            ),
            observed,
        )
        self.assertFalse(result.safe)
        self.assertEqual(len(result.unknowns), 1)
        self.assertEqual(observed.row_counts["projects"], 3)

    def test_identifier_validation_rejects_unsafe_names(self):
        with self.assertRaises(ValueError):
            PostgresDataSafetyObserver(
                lambda: FakeConnection(),
                DataSafetyObservationTargets(tables=("projects;DROP TABLE x",)),
            ).observe()


if __name__ == "__main__":
    unittest.main()
