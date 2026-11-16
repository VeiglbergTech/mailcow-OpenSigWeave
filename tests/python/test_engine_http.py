"""HTTP boundary smoke tests for the embedded engine."""
import os
import sys
import unittest
import tempfile
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
os.environ.setdefault("SIGNATURE_STORAGE_PATH", str(Path(tempfile.gettempdir()) / "opensigweave-engine-http-tests"))

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from fastapi.testclient import TestClient
from app import app, SessionLocal, DATABASE_ENGINE
from models import DomainDB


class EngineHttpBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        command.upgrade(Config(str(ENGINE_PATH / "alembic.ini")), "head")
        # Minimal stand-in for Mailcow's authoritative domain table.
        with DATABASE_ENGINE.begin() as connection:
            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS domain ("
                "domain VARCHAR(255) PRIMARY KEY, active TINYINT(1) NOT NULL)"
            ))

    @classmethod
    def tearDownClass(cls):
        # Keep the disposable Mailcow stand-in out of the isolated schema tests.
        with DATABASE_ENGINE.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS domain"))

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

    def test_signature_endpoint_renders_using_mailcow_profile_and_domain(self):
        domain_name = f"engine-{os.getpid()}-{id(self)}.example.test"
        sender = f"alice@{domain_name}"
        with DATABASE_ENGINE.begin() as connection:
            connection.execute(
                text("INSERT INTO domain (domain, active) VALUES (:domain, 1)"),
                {"domain": domain_name},
            )
        with SessionLocal() as session:
            domain = DomainDB(
                domain_name=domain_name,
                is_active=True,
                template_html="<p>Hello {{ first_name }} — {{ phone }}</p>",
            )
            session.add(domain)
            session.commit()

        try:
            response = self.client.post(
                "/internal/signature",
                headers={"X-Engine-Key": os.environ["ENGINE_API_KEY"]},
                json={
                    "authenticated_user": sender,
                    "envelope_sender": sender,
                    "target_domain": domain_name,
                    "profile": {
                        "first_name": "<Ada>",
                        "last_name": "Lovelace",
                        "phone": "+49 123 456",
                    },
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertIn("&lt;Ada&gt;", payload["html"])
            self.assertIn("+49 123 456", payload["html"])
            self.assertFalse(payload["inject_on_replies"])
        finally:
            with SessionLocal() as session:
                stored = session.query(DomainDB).filter_by(domain_name=domain_name).one_or_none()
                if stored is not None:
                    session.delete(stored)
                    session.commit()
            with DATABASE_ENGINE.begin() as connection:
                connection.execute(
                    text("DELETE FROM domain WHERE domain = :domain"),
                    {"domain": domain_name},
                )

    def test_inactive_mailcow_domain_never_receives_signature(self):
        domain_name = f"inactive-{os.getpid()}-{id(self)}.example.test"
        sender = f"alice@{domain_name}"
        with DATABASE_ENGINE.begin() as connection:
            connection.execute(
                text("INSERT INTO domain (domain, active) VALUES (:domain, 0)"),
                {"domain": domain_name},
            )
        with SessionLocal() as session:
            session.add(DomainDB(
                domain_name=domain_name,
                is_active=True,
                template_html="<p>This must never be injected</p>",
            ))
            session.commit()
        try:
            response = self.client.post(
                "/internal/signature",
                headers={"X-Engine-Key": os.environ["ENGINE_API_KEY"]},
                json={
                    "authenticated_user": sender,
                    "envelope_sender": sender,
                    "target_domain": domain_name,
                    "profile": {},
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["html"], "")
        finally:
            with SessionLocal() as session:
                stored = session.query(DomainDB).filter_by(domain_name=domain_name).one_or_none()
                if stored is not None:
                    session.delete(stored)
                    session.commit()
            with DATABASE_ENGINE.begin() as connection:
                connection.execute(
                    text("DELETE FROM domain WHERE domain = :domain"),
                    {"domain": domain_name},
                )


if __name__ == "__main__":
    unittest.main()
