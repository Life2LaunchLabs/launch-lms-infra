import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "feedback_diagnose", ROOT / "deploy/feedback-migration/diagnose.py"
)
diagnose = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnose)


class FakeJira:
    project = "FEED"

    def __init__(self, pages):
        self.pages = iter(pages)

    def _request(self, method, path, payload):
        assert method == "POST" and path == "/rest/api/3/search/jql"
        return next(self.pages)


class FeedbackDiagnosisTests(unittest.TestCase):
    def test_legacy_uuid_shape_is_sanitized(self):
        self.assertEqual("missing", diagnose.legacy_uuid_shape(""))
        self.assertEqual("non_string", diagnose.legacy_uuid_shape(123))
        self.assertEqual("canonical_user_uuid", diagnose.legacy_uuid_shape(
            "user_12345678-1234-4234-8234-123456789abc"
        ))
        self.assertEqual("user_prefix_non_uuid", diagnose.legacy_uuid_shape("user_old"))
        self.assertEqual("other_nonempty_string", diagnose.legacy_uuid_shape("historical"))

    def test_inventory_search_paginates_all_feed_issues(self):
        issues = diagnose.all_feed_issues(FakeJira([
            {"issues": [{"key": "FEED-1"}], "isLast": False, "nextPageToken": "second"},
            {"issues": [{"key": "FEED-2"}], "isLast": True},
        ]))
        self.assertEqual(["FEED-1", "FEED-2"], [issue["key"] for issue in issues])

    def test_incomplete_or_repeated_pagination_fails_closed(self):
        for page in (
            {"issues": [], "isLast": False},
            {"issues": [], "isLast": False, "nextPageToken": "first"},
        ):
            pages = [page] if "nextPageToken" not in page else [page, page]
            with self.subTest(page=page), self.assertRaises(ValueError):
                diagnose.all_feed_issues(FakeJira(pages))

    def test_missing_rows_are_classified_without_emitting_identity_values(self):
        issue = {"key": "FEED-1", "fields": {"labels": ["launchlms-feedback", "launchlms-org-2"]}}
        legacy = {"source": "unstable", "user_id": 7, "org_id": 2, "user_uuid": "private-uuid"}
        self.assertEqual(["user_row_missing", "orphan_legacy_uuid_other_nonempty_string"], diagnose.classify(
            issue, legacy, None, SimpleNamespace(org_uuid="org-private")
        ))
        self.assertEqual(["organization_uuid_missing"], diagnose.classify(
            issue, legacy, SimpleNamespace(user_uuid="private-uuid"), SimpleNamespace(org_uuid="")
        ))

    def test_legacy_and_label_gaps_are_visible(self):
        issue = {"key": "FEED-1", "fields": {"labels": ["launchlms-feedback"]}}
        self.assertEqual(["labeled_without_legacy_property"], diagnose.classify(issue, None, None, None))
        self.assertIn("organization_label_mismatch", diagnose.classify(
            issue, {"source": "unstable", "user_id": 7, "org_id": 2, "user_uuid": "u"},
            SimpleNamespace(user_uuid="u"), SimpleNamespace(org_uuid="o")
        ))


if __name__ == "__main__":
    unittest.main()
