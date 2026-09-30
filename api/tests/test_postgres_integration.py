import copy
import os
from pathlib import Path
import unittest

import psycopg
from psycopg.rows import dict_row

from app.core.compatibility import (
    CompatibilityPolicy,
    ObservedDatabaseCompatibility,
    inspect_compatibility,
)
from app.core.data_safety import (
    DataSafetyPolicy,
    SequencePolicy,
    evaluate_data_safety,
)
from app.core.data_safety_observer import (
    DataSafetyObservationTargets,
    ForeignKeyObservation,
    PostgresDataSafetyObserver,
)
from app.core.schema_catalog import PostgresCatalogReader
from app.core.schema_drift import compare_schema_drift
from app.core.schema_fingerprint import (
    compare_schema,
    schema_fingerprint,
    semantic_representation,
)
from app.core.schema_validation import PASS, build_schema_validation_report
from app.core.schema_representation import canonicalize_schema


@unittest.skipUnless(
    os.getenv("AIHUB_POSTGRES_TEST_DSN"),
    "controlled PostgreSQL integration DSN is not configured",
)
class ControlledPostgresIntegrationHarness(unittest.TestCase):
    """Validation against an explicitly supplied disposable PostgreSQL 17 DSN."""

    dsn = os.environ.get("AIHUB_POSTGRES_TEST_DSN")

    @classmethod
    def setUpClass(cls):
        cls.connection_factory = staticmethod(
            lambda: psycopg.connect(cls.dsn, row_factory=dict_row)
        )
        migration = (
            Path(__file__).parents[2] / "migrations" / "0001_baseline.sql"
        ).read_text()
        with cls.connection_factory() as connection:
            connection.execute(migration)
            connection.execute(
                """
                INSERT INTO public.projects (name, slug)
                VALUES ('Phase N', 'phase-n')
                RETURNING id
                """
            )
            project_id = connection.execute("SELECT currval('public.projects_id_seq')").fetchone()["currval"]
            connection.execute(
                """
                INSERT INTO public.documents
                    (project_id, title, filename, status)
                VALUES (%s, 'Fixture document', 'fixture.txt', 'active')
                RETURNING id
                """,
                (project_id,),
            )
            document_id = connection.execute("SELECT currval('public.documents_id_seq')").fetchone()["currval"]
            connection.execute(
                """
                INSERT INTO public.memories (content, project_id, embedding)
                VALUES ('Fixture memory', %s, %s)
                """,
                (project_id, "[" + ",".join(["0"] * 384) + "]"),
            )
            connection.execute(
                """
                INSERT INTO public.document_chunks
                    (document_id, chunk_index, content, embedding)
                VALUES (%s, 0, 'Fixture chunk', %s)
                """,
                (document_id, "[" + ",".join(["0"] * 384) + "]"),
            )

    def test_controlled_postgres_17_catalog_and_validation(self):
        with self.connection_factory() as connection:
            server = connection.execute(
                "SELECT current_database() AS database_name, "
                "current_schema() AS schema_name, "
                "current_setting('server_version_num')::int AS version_num"
            ).fetchone()
            extension = connection.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            ).fetchone()
            self.assertEqual(server["database_name"], "phase_n")
            self.assertEqual(server["schema_name"], "public")
            self.assertEqual(server["version_num"] // 10000, 17)
            self.assertIsNotNone(extension)
            vector_version = extension["extversion"]

        snapshot = PostgresCatalogReader(self.connection_factory).read_snapshot()
        actual = snapshot.as_schema_rows()
        canonical = canonicalize_schema(actual)
        self.assertEqual(canonicalize_schema(canonical), canonical)
        self.assertEqual(snapshot.database, "phase_n")
        self.assertEqual(snapshot.schema, "public")
        self.assertEqual(
            {row["name"] for row in actual["tables"]},
            {"projects", "memories", "documents", "document_chunks"},
        )
        self.assertTrue(
            any(
                row["type"] == "vector(384)"
                for row in actual["columns"]
                if row["name"] == "embedding"
            )
        )

        constraints = {row["name"]: row for row in actual["constraints"]}
        self.assertEqual(
            constraints["document_chunks_document_id_fkey"]["on_delete"],
            "CASCADE",
        )
        self.assertEqual(
            constraints["documents_project_id_fkey"]["on_delete"], "SET NULL"
        )
        indexes = {row["name"]: row for row in actual["indexes"]}
        self.assertTrue(indexes["idx_documents_project_relative_path"]["definition"])
        self.assertEqual(indexes["idx_documents_project_relative_path"]["access_method"], "btree")
        self.assertIsNone(indexes["idx_documents_project_relative_path"]["predicate"])
        self.assertIsNone(indexes["idx_documents_project_relative_path"]["expressions"])
        sequences = {row["name"]: row for row in actual["sequences"]}
        self.assertEqual(sequences["projects_id_seq"]["owner_column"], "id")

        with self.connection_factory() as connection:
            direct = connection.execute(
                """
                SELECT pg_get_constraintdef(oid, true) AS definition
                FROM pg_constraint
                WHERE conname = 'document_chunks_document_id_fkey'
                """
            ).fetchone()
            index = connection.execute(
                """
                SELECT pg_get_indexdef(indexrelid) AS definition,
                       pg_get_expr(indpred, indrelid) AS predicate,
                       pg_get_expr(indexprs, indrelid) AS expressions
                FROM pg_index
                WHERE indexrelid = 'public.idx_documents_project_relative_path'::regclass
                """
            ).fetchone()
            self.assertIn("ON DELETE CASCADE", direct["definition"])
            self.assertIn("idx_documents_project_relative_path", index["definition"])
            self.assertIsNone(index["predicate"])
            self.assertIsNone(index["expressions"])

        compatibility = inspect_compatibility(
            ObservedDatabaseCompatibility.from_snapshot(snapshot, postgres_major=17),
            CompatibilityPolicy(
                postgres_majors=frozenset({17}),
                vector_versions=frozenset({vector_version}),
                expected_extension_schema="public",
                expected_vector_dimension=384,
            ),
        )
        self.assertTrue(compatibility.compatible)

        targets = DataSafetyObservationTargets(
            tables=("projects", "memories", "documents", "document_chunks"),
            required_columns=(
                ("projects", "name"),
                ("memories", "content"),
                ("documents", "title"),
                ("document_chunks", "content"),
            ),
            relationships=(
                ForeignKeyObservation(
                    "memories.project_id->projects.id", "memories", "project_id",
                    "projects", "id",
                ),
                ForeignKeyObservation(
                    "documents.project_id->projects.id", "documents", "project_id",
                    "projects", "id",
                ),
                ForeignKeyObservation(
                    "document_chunks.document_id->documents.id", "document_chunks",
                    "document_id", "documents", "id",
                ),
            ),
            sequences=tuple(sequences),
        )
        observed = PostgresDataSafetyObserver(
            self.connection_factory, targets
        ).observe()
        safety = evaluate_data_safety(
            DataSafetyPolicy(
                expected_database="phase_n",
                expected_schema="public",
                expected_row_counts={
                    "projects": 1,
                    "memories": 1,
                    "documents": 1,
                    "document_chunks": 1,
                },
                max_null_counts={
                    ("projects", "name"): 0,
                    ("memories", "content"): 0,
                    ("documents", "title"): 0,
                    ("document_chunks", "content"): 0,
                },
                max_orphan_counts={
                    "memories.project_id->projects.id": 0,
                    "documents.project_id->projects.id": 0,
                    "document_chunks.document_id->documents.id": 0,
                },
                sequence_policies={
                    name: SequencePolicy(expected_owner=f"public.{name.removesuffix('_id_seq')}.id")
                    for name in sequences
                },
            ),
            observed,
        )
        self.assertTrue(safety.safe)

        report = build_schema_validation_report(
            semantic_fingerprint=schema_fingerprint(actual),
            drift=compare_schema_drift(actual, actual),
            compatibility=compatibility,
            data_safety=safety,
            evidence_source="controlled_postgresql",
        )
        self.assertEqual(report.status, PASS)
        self.assertEqual(report.evidence_source, "controlled_postgresql")
        self.assertTrue(report.valid)

        renamed = copy.deepcopy(actual)
        renamed["indexes"][0]["name"] += "_renamed"
        comparison = compare_schema(actual, renamed)
        self.assertTrue(comparison.semantic_match)
        self.assertFalse(comparison.eligible_without_reconciliation)
        drifted = copy.deepcopy(actual)
        drifted["columns"][0]["type"] = "vector(768)"
        self.assertFalse(compare_schema_drift(actual, drifted).semantic_match)
        self.assertEqual(
            schema_fingerprint(actual),
            schema_fingerprint(semantic_representation(actual)),
        )

    def test_observers_do_not_mutate_and_transactions_are_read_only(self):
        def state():
            with self.connection_factory() as connection:
                return connection.execute(
                    """
                    SELECT
                        (SELECT count(*) FROM public.projects) AS rows,
                        (SELECT last_value FROM public.projects_id_seq) AS sequence
                    """
                ).fetchone()

        before = state()
        PostgresCatalogReader(self.connection_factory).read_snapshot()
        PostgresDataSafetyObserver(
            self.connection_factory,
            DataSafetyObservationTargets(tables=("projects",), sequences=("projects_id_seq",)),
        ).observe()
        after = state()
        self.assertEqual(after, before)

        with self.connection_factory() as connection:
            connection.execute("BEGIN; SET TRANSACTION READ ONLY;")
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                connection.execute(
                    "INSERT INTO public.projects (name, slug) VALUES ('blocked', 'blocked')"
                )
            connection.execute("ROLLBACK;")
        self.assertEqual(state(), before)


if __name__ == "__main__":
    unittest.main()
