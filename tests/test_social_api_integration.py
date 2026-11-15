"""Exercise additive analyze context and conditional links without provider calls."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from backend import main, models


class AnalyzeSocialIntegration(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        main.app.dependency_overrides[main.current_user] = lambda: {"_id": "test-user"}
        self.addCleanup(main.app.dependency_overrides.clear)

    def analyze(self, root, context=None):
        result = models.AnalyzeResponse(status="ok", verdict_id="test-verdict", verdict="good_buy",
                                        reason="A useful basic.", social_context=context)
        record = {"signals": {"timings": {}}, "attributes": None}
        with mock.patch.object(main, "ROOT", root), mock.patch.object(main, "decide", return_value=(result, record)):
            return self.client.post("/analyze", json={"item": {"name": "Barrel jeans"}})

    def test_missing_context_preserves_existing_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            response = self.analyze(Path(folder))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["verdict"], "good_buy")
        self.assertEqual(response.json()["reason"], "A useful basic.")
        self.assertIsNone(response.json()["social_context"])

    def test_no_link_when_page_is_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            response = self.analyze(Path(folder), {"term": "barrel jeans", "similarity": 0.8})
        self.assertIsNone(response.json()["social_context"]["trends_ui_url"])

    def test_existing_page_gets_encoded_link_on_backend_origin(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "dashboard").mkdir()
            (root / "dashboard" / "trends.html").write_text("<!doctype html>")
            response = self.analyze(root, {"term": "barrel jeans", "similarity": 0.8})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["social_context"]["trends_ui_url"],
                         "http://testserver/trends-ui?term=barrel+jeans")


if __name__ == "__main__":
    unittest.main()
