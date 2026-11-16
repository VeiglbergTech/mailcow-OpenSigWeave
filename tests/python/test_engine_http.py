"""HTTP boundary smoke tests for the embedded engine."""
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_PATH = REPO_ROOT / "data" / "Dockerfiles" / "opensigweave"
sys.path.insert(0, str(ENGINE_PATH))

os.environ.setdefault("DBNAME", "mailcow")
os.environ.setdefault("DBUSER", "mailcow")
os.environ.setdefault("DBPASS", "mailcow-test-password")
os.environ.setdefault("OPENSIGWEAVE_DB_HOST", "127.0.0.1")
os.environ.setdefault("OPENSIGWEAVE_DB_PORT", "3306")
os.environ.setdefault("ENGINE_API_KEY", "test-engine-key-that-is-at-least-32-characters-long")

from fastapi.testclient import TestClient
from app import app


class EngineHttpBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_is_available_without_database_access(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_signature_endpoint_rejects_missing_engine_key(self):
        response = self.client.post(
            "/internal/signature",
            json={"authenticated_user": "alice@example.test", "envelope_sender": "alice@example.test", "target_domain": "example.test", "profile": {}},
        )
        self.assertEqual(response.status_code, 401)

    def test_signature_endpoint_rejects_invalid_sender_after_authentication(self):
        response = self.client.post(
            "/internal/signature",
            headers={"X-Engine-Key": os.environ["ENGINE_API_KEY"]},
            json={"authenticated_user": "alice@example.test", "envelope_sender": "not-an-email", "target_domain": "example.test", "profile": {}},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
