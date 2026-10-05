import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.routers import memories


class FakeResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, project_row=(2,)):
        self.project_row = project_row
        self.calls = []
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        normalized_query = " ".join(query.split())
        self.calls.append((normalized_query, params))

        if normalized_query.startswith("SELECT") and "FROM memories" in normalized_query:
            return FakeResult(("existing content", "fact", None, 3, "api", 1))
        if normalized_query.startswith("SELECT") and "FROM projects" in normalized_query:
            return FakeResult(self.project_row)
        if normalized_query.startswith("UPDATE memories"):
            return FakeResult((7, "existing content", "fact", None, 3, "api", "updated"))
        raise AssertionError(f"Unexpected SQL: {normalized_query}")

    def commit(self):
        self.committed = True


class MemoryPatchProjectIdTests(unittest.TestCase):
    def make_update(self, payload):
        if hasattr(memories.MemoryUpdate, "model_validate"):
            return memories.MemoryUpdate.model_validate(payload)
        return memories.MemoryUpdate.parse_obj(payload)

    def call_update(self, payload, project_row=(2,)):
        connection = FakeConnection(project_row=project_row)
        with (
            patch.object(memories, "get_db_connection", return_value=connection),
            patch.object(memories.model, "encode", return_value=MagicMock(tolist=lambda: [0.1, 0.2])),
            patch.object(memories, "Vector", side_effect=lambda value: value),
        ):
            response = memories.update_memory(7, self.make_update(payload))
        update_calls = [
            (query, params)
            for query, params in connection.calls
            if query.startswith("UPDATE memories")
        ]
        return response, connection, update_calls

    def test_changes_project_id_to_existing_project(self):
        _response, connection, updates = self.call_update({"project_id": 2})

        self.assertEqual(len(updates), 1)
        self.assertIn("project_id = %s", updates[0][0])
        self.assertEqual(updates[0][1][5], 2)
        self.assertTrue(connection.committed)

    def test_null_project_id_unlinks_memory(self):
        _response, _connection, updates = self.call_update({"project_id": None})

        self.assertEqual(len(updates), 1)
        self.assertIsNone(updates[0][1][5])

    def test_omitted_project_id_preserves_existing_project(self):
        _response, _connection, updates = self.call_update({})

        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0][1][5], 1)

    def test_unknown_project_returns_404_without_update(self):
        connection = FakeConnection(project_row=None)
        with (
            patch.object(memories, "get_db_connection", return_value=connection),
            patch.object(memories.model, "encode", return_value=MagicMock(tolist=lambda: [0.1, 0.2])),
            patch.object(memories, "Vector", side_effect=lambda value: value),
        ):
            with self.assertRaises(HTTPException) as raised:
                memories.update_memory(7, self.make_update({"project_id": 999}))

        self.assertEqual(raised.exception.status_code, 404)
        self.assertFalse(
            any(query.startswith("UPDATE memories") for query, _ in connection.calls)
        )
        self.assertFalse(connection.committed)


if __name__ == "__main__":
    unittest.main()
