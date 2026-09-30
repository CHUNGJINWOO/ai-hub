import re
import unittest
from pathlib import Path


SQL_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "sql"
    / "production_db_audit.sql"
)


class ProductionDbAuditPlanTests(unittest.TestCase):
    @staticmethod
    def remove_sql_comments_and_literals(sql: str) -> str:
        sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
        sql = re.sub(r"--[^\n]*", "", sql)
        return re.sub(r"'(?:''|[^'])*'", "''", sql)

    def test_audit_sql_is_read_only_and_transactional(self):
        sql = SQL_PATH.read_text(encoding="utf-8")

        self.assertIn("BEGIN;", sql)
        self.assertIn("SET TRANSACTION READ ONLY;", sql)
        self.assertIn("ROLLBACK;", sql)

        destructive_statements = (
            "CREATE",
            "ALTER",
            "DROP",
            "INSERT",
            "UPDATE",
            "DELETE",
            "TRUNCATE",
            "GRANT",
            "REVOKE",
        )
        sanitized_sql = self.remove_sql_comments_and_literals(sql)
        for statement in sanitized_sql.split(";"):
            first_token = statement.strip().split(maxsplit=1)[:1]
            if first_token:
                self.assertNotIn(first_token[0].upper(), destructive_statements)

    def test_audit_sql_only_counts_application_rows(self):
        sql = SQL_PATH.read_text(encoding="utf-8")

        self.assertIn("projects", sql)
        self.assertIn("memories", sql)
        self.assertIn("documents", sql)
        self.assertIn("document_chunks", sql)
        self.assertNotRegex(
            sql,
            r"\bSELECT\s+\*\s+FROM\s+"
            r"(projects|memories|documents|document_chunks)\b",
        )

    def test_metadata_candidates_are_present_in_sql(self):
        sql = SQL_PATH.read_text(encoding="utf-8")
        for table_name in (
            "alembic_version",
            "schema_migrations",
            "migrations",
            "migration",
            "flyway_schema_history",
        ):
            self.assertIn(f"'{table_name}'", sql)


if __name__ == "__main__":
    unittest.main()
