import unittest
from unittest.mock import patch

from fastapi import Request
from fastapi.testclient import TestClient

from app.core.authorization import AuthorizationContext, AuthorizationDenied
from app.main import app
from app.routers import projects as projects_router


class ProjectAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.writer_context = AuthorizationContext(
            identity="writer",
            allowed_project_ids=frozenset({1, 2}),
            allow_write=True,
        )
        self.reader_context = AuthorizationContext(
            identity="reader",
            allowed_project_ids=frozenset({1}),
            allow_global_read=False,
            allow_write=False,
        )
        self.global_reader_context = AuthorizationContext(
            identity="global-reader",
            allowed_project_ids=frozenset(),
            allow_global_read=True,
            allow_write=False,
        )

    def test_create_project_allows_write_context(self):
        request = Request({"type": "http", "state": {"authorization_context": self.writer_context}})
        payload = projects_router.ProjectCreate(name="New Project", slug="new-proj")

        fake_row = (10, "New Project", "new-proj", None, "active", "2026-10-05", "2026-10-05")

        with patch("app.routers.projects.get_db_connection") as mock_get_db:
            conn = mock_get_db.return_value.__enter__.return_value
            conn.execute.side_effect = [
                unittest.mock.MagicMock(fetchone=lambda: None),  # slug check
                unittest.mock.MagicMock(fetchone=lambda: fake_row),  # insert
            ]
            response = projects_router.create_project(payload, request=request)

        self.assertEqual(response["id"], 10)
        self.assertEqual(response["slug"], "new-proj")

    def test_create_project_denies_reader_context(self):
        request = Request({"type": "http", "state": {"authorization_context": self.reader_context}})
        payload = projects_router.ProjectCreate(name="New Project", slug="new-proj")

        with self.assertRaises(Exception) as raised:
            projects_router.create_project(payload, request=request)

        self.assertEqual(raised.exception.status_code, 403)

    def test_list_projects_global_read(self):
        request = Request({"type": "http", "state": {"authorization_context": self.global_reader_context}})

        fake_list = {
            "count": 2,
            "projects": [
                {"id": 1, "name": "P1", "slug": "p1"},
                {"id": 2, "name": "P2", "slug": "p2"},
            ],
        }

        with patch("app.routers.projects.fetch_projects", return_value=fake_list) as mock_fetch:
            response = projects_router.list_projects(request=request)

        self.assertEqual(response["count"], 2)
        mock_fetch.assert_called_once_with()

    def test_list_projects_project_scoped_access(self):
        request = Request({"type": "http", "state": {"authorization_context": self.reader_context}})

        fake_list = {
            "count": 1,
            "projects": [{"id": 1, "name": "P1", "slug": "p1"}],
        }

        with patch("app.routers.projects.fetch_projects", return_value=fake_list) as mock_fetch:
            response = projects_router.list_projects(request=request)

        self.assertEqual(response["count"], 1)
        mock_fetch.assert_called_once_with(allowed_project_ids=frozenset({1}))

    def test_get_project_allows_authorized_project(self):
        request = Request({"type": "http", "state": {"authorization_context": self.reader_context}})
        fake_row = (1, "P1", "p1", "desc", "active", "2026-10-05", "2026-10-05", 3)

        with patch("app.routers.projects.get_db_connection") as mock_get_db:
            conn = mock_get_db.return_value.__enter__.return_value
            conn.execute.return_value.fetchone.return_value = fake_row

            response = projects_router.get_project(1, request=request)

        self.assertEqual(response["id"], 1)
        self.assertEqual(response["memory_count"], 3)

    def test_get_project_denies_unauthorized_project(self):
        request = Request({"type": "http", "state": {"authorization_context": self.reader_context}})

        with self.assertRaises(Exception) as raised:
            projects_router.get_project(2, request=request)

        self.assertEqual(raised.exception.status_code, 403)

    def test_update_project_allows_writer_for_authorized_project(self):
        request = Request({"type": "http", "state": {"authorization_context": self.writer_context}})
        payload = projects_router.ProjectUpdate(name="Updated P2")
        fake_current = ("P2", "p2", "desc", "active")
        fake_updated = (2, "Updated P2", "p2", "desc", "active", "2026-10-05", "2026-10-05")

        with patch("app.routers.projects.get_db_connection") as mock_get_db:
            conn = mock_get_db.return_value.__enter__.return_value
            conn.execute.side_effect = [
                unittest.mock.MagicMock(fetchone=lambda: fake_current),
                unittest.mock.MagicMock(fetchone=lambda: None),  # slug check
                unittest.mock.MagicMock(fetchone=lambda: fake_updated),
            ]

            response = projects_router.update_project(2, payload, request=request)

        self.assertEqual(response["id"], 2)
        self.assertEqual(response["name"], "Updated P2")

    def test_update_project_denies_unauthorized_project(self):
        request = Request({"type": "http", "state": {"authorization_context": self.reader_context}})
        payload = projects_router.ProjectUpdate(name="Updated P2")

        with self.assertRaises(Exception) as raised:
            projects_router.update_project(2, payload, request=request)

        self.assertEqual(raised.exception.status_code, 403)

    def test_delete_project_allows_writer_for_authorized_project(self):
        request = Request({"type": "http", "state": {"authorization_context": self.writer_context}})
        fake_row = (2, "P2", "p2")

        with patch("app.routers.projects.get_db_connection") as mock_get_db:
            conn = mock_get_db.return_value.__enter__.return_value
            conn.execute.return_value.fetchone.return_value = fake_row

            response = projects_router.delete_project(2, request=request)

        self.assertEqual(response["status"], "deleted")
        self.assertEqual(response["id"], 2)

    def test_delete_project_denies_unauthorized_project(self):
        request = Request({"type": "http", "state": {"authorization_context": self.reader_context}})

        with self.assertRaises(Exception) as raised:
            projects_router.delete_project(2, request=request)

        self.assertEqual(raised.exception.status_code, 403)


class ProjectAuthorizationHttpTests(unittest.TestCase):
    def client(self, auth_context):
        client = TestClient(app)
        client.app_state["authorization_context"] = auth_context
        return client

    def test_http_post_projects(self):
        writer = AuthorizationContext(identity="w", allowed_project_ids=frozenset(), allow_write=True)
        reader = AuthorizationContext(identity="r", allowed_project_ids=frozenset(), allow_write=False)

        fake_row = (10, "New Proj", "new-proj", None, "active", "2026-10-05", "2026-10-05")

        with (
            patch("app.main.ensure_memories_table"),
            patch("app.main.ensure_projects_table"),
            patch("app.routers.projects.get_db_connection") as mock_get_db,
        ):
            conn = mock_get_db.return_value.__enter__.return_value
            conn.execute.side_effect = [
                unittest.mock.MagicMock(fetchone=lambda: None),
                unittest.mock.MagicMock(fetchone=lambda: fake_row),
            ]

            with self.client(writer) as client:
                res = client.post("/projects", json={"name": "New Proj", "slug": "new-proj"})
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res.json()["id"], 10)

            with self.client(reader) as client:
                res = client.post("/projects", json={"name": "New Proj", "slug": "new-proj"})
                self.assertEqual(res.status_code, 403)


if __name__ == "__main__":
    unittest.main()
