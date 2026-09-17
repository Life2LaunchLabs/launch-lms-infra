"""Publication and environment isolation for platform announcements."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps/control-plane/api"))

import announcements
from main import app
from models import Base, Project


class AnnouncementTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(Project(id="launch-lms", manifest_revision="test", enabled=True))
            session.commit()

    def tearDown(self):
        self.engine.dispose()

    def test_operator_draft_is_invisible_until_publication_and_only_in_its_environment(self):
        with Session(self.engine) as session:
            item = announcements.draft_announcement(
                "launch-lms", "unstable", announcements.AnnouncementDraft(title=" Update ", body=" Hello "),
                {"login": "operator"}, session,
            )
            self.assertEqual((item["title"], item["body"], item["published_at"]), ("Update", "Hello", None))
            self.assertEqual(announcements.embed_announcements(
                SimpleNamespace(project_id="launch-lms", environment="unstable"), session,
            ), [])
            with self.assertRaises(HTTPException) as wrong:
                announcements.publish_announcement("launch-lms", "production", item["id"], {}, session)
            self.assertEqual(wrong.exception.status_code, 404)
            published = announcements.publish_announcement("launch-lms", "unstable", item["id"], {}, session)
            self.assertIsNotNone(published["published_at"])
            self.assertEqual(announcements.publish_announcement(
                "launch-lms", "unstable", item["id"], {}, session,
            ), published)
            self.assertEqual([entry["id"] for entry in announcements.embed_announcements(
                SimpleNamespace(project_id="launch-lms", environment="unstable"), session,
            )], [item["id"]])
            self.assertEqual(announcements.embed_announcements(
                SimpleNamespace(project_id="launch-lms", environment="production"), session,
            ), [])

    def test_empty_draft_unknown_project_and_route_auth_contract(self):
        with Session(self.engine) as session:
            for title, body in ((" ", "Text"), ("Title", " ")):
                with self.assertRaises(HTTPException) as invalid:
                    announcements.draft_announcement(
                        "launch-lms", "unstable", announcements.AnnouncementDraft(title=title, body=body), {}, session,
                    )
                self.assertEqual(invalid.exception.status_code, 422)
            with self.assertRaises(HTTPException) as missing:
                announcements.draft_announcement(
                    "unknown", "unstable", announcements.AnnouncementDraft(title="Title", body="Text"), {}, session,
                )
            self.assertEqual(missing.exception.status_code, 404)
        routes = {route.path: route for route in app.routes if hasattr(route, "dependant")}
        operator = routes["/api/v1/projects/{project_id}/environments/{environment}/announcements"]
        embed = routes["/api/v1/embed/announcements"]
        self.assertIn("require_operator", {item.call.__name__ for item in operator.dependant.dependencies})
        self.assertIn("require_embed_session", {item.call.__name__ for item in embed.dependant.dependencies})
