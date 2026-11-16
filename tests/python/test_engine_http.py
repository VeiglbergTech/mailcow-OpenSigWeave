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
import app as app_module
from app import app, SessionLocal, DATABASE_ENGINE
from models import (
    DomainDB, SignatureDB, SignatureDomainDB, SignatureVariantDB, SignatureVersionDB,
)
from signature_storage import SignatureFileStore


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
        self.tempdir = tempfile.TemporaryDirectory()
        self.previous_store = app_module.signature_file_store
        app_module.signature_file_store = SignatureFileStore(self.tempdir.name)

    def tearDown(self):
        app_module.signature_file_store = self.previous_store
        self.tempdir.cleanup()

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


    def test_signature_management_api_requires_engine_key(self):
        response = self.client.get("/internal/admin/domains/example.test/signatures")
        self.assertEqual(response.status_code, 401)

    def test_signature_management_create_edit_version_restore_and_scope(self):
        domain_name = f"manage-{os.getpid()}-{id(self)}.example.test"
        with DATABASE_ENGINE.begin() as connection:
            connection.execute(
                text("INSERT INTO domain (domain, active) VALUES (:domain, 1)"),
                {"domain": domain_name},
            )

        headers = {"X-Engine-Key": os.environ["ENGINE_API_KEY"]}
        signature_id = None
        try:
            created = self.client.post(
                f"/internal/admin/domains/{domain_name}/signatures",
                headers=headers,
                json={
                    "name": "Corporate",
                    "mode": "PROFESSIONAL",
                    "html_content": "<p>Hello {{ first_name }}</p><script>alert(1)</script>",
                    "created_by": "admin@example.test",
                },
            )
            self.assertEqual(created.status_code, 201, created.text)
            payload = created.json()
            signature_id = payload["id"]
            self.assertEqual(payload["latest_version"], 1)

            listed = self.client.get(
                f"/internal/admin/domains/{domain_name}/signatures",
                headers=headers,
            )
            self.assertEqual(listed.status_code, 200, listed.text)
            self.assertEqual([item["id"] for item in listed.json()["signatures"]], [signature_id])

            detail = self.client.get(
                f"/internal/admin/domains/{domain_name}/signatures/{signature_id}",
                headers=headers,
            )
            self.assertEqual(detail.status_code, 200, detail.text)
            self.assertIn("Hello", detail.json()["html_content"])
            self.assertNotIn("<script", detail.json()["html_content"])

            added = self.client.post(
                f"/internal/admin/domains/{domain_name}/signatures/{signature_id}/versions",
                headers=headers,
                json={"html_content": "<p>Version two</p>", "created_by": "admin@example.test"},
            )
            self.assertEqual(added.status_code, 201, added.text)
            self.assertEqual(added.json()["version_number"], 2)

            versions = self.client.get(
                f"/internal/admin/domains/{domain_name}/signatures/{signature_id}/versions",
                headers=headers,
            )
            self.assertEqual(versions.status_code, 200, versions.text)
            self.assertEqual([item["version_number"] for item in versions.json()["versions"]], [2, 1])
            first_version_id = versions.json()["versions"][1]["id"]

            restored = self.client.post(
                f"/internal/admin/domains/{domain_name}/signatures/{signature_id}/restore/{first_version_id}",
                headers=headers,
            )
            self.assertEqual(restored.status_code, 201, restored.text)
            self.assertEqual(restored.json()["version_number"], 3)

            updated = self.client.put(
                f"/internal/admin/domains/{domain_name}/signatures/{signature_id}",
                headers=headers,
                json={"is_active": False},
            )
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertFalse(updated.json()["is_active"])

            wrong_domain = self.client.get(
                f"/internal/admin/domains/other-{domain_name}/signatures/{signature_id}",
                headers=headers,
            )
            self.assertEqual(wrong_domain.status_code, 404)
        finally:
            if signature_id is not None:
                with SessionLocal() as session:
                    session.execute(
                        text("DELETE FROM osw_signature_versions WHERE signature_id = :id"),
                        {"id": signature_id},
                    )
                    session.execute(
                        text("DELETE FROM osw_signature_variants WHERE signature_id = :id"),
                        {"id": signature_id},
                    )
                    session.execute(
                        text("DELETE FROM osw_signature_domains WHERE signature_id = :id"),
                        {"id": signature_id},
                    )
                    session.execute(
                        text("DELETE FROM osw_signatures WHERE id = :id"),
                        {"id": signature_id},
                    )
                    session.execute(
                        text("DELETE FROM osw_domains WHERE domain_name = :domain"),
                        {"domain": domain_name},
                    )
                    session.commit()
            with DATABASE_ENGINE.begin() as connection:
                connection.execute(
                    text("DELETE FROM domain WHERE domain = :domain"),
                    {"domain": domain_name},
                )

    def test_signature_management_rejects_inactive_mailcow_domains(self):
        domain_name = f"manage-inactive-{os.getpid()}-{id(self)}.example.test"
        with DATABASE_ENGINE.begin() as connection:
            connection.execute(
                text("INSERT INTO domain (domain, active) VALUES (:domain, 0)"),
                {"domain": domain_name},
            )
        try:
            response = self.client.post(
                f"/internal/admin/domains/{domain_name}/signatures",
                headers={"X-Engine-Key": os.environ["ENGINE_API_KEY"]},
                json={
                    "name": "Must not be created",
                    "mode": "AUTO",
                    "html_content": "<p>Inactive</p>",
                },
            )
            self.assertEqual(response.status_code, 404)
        finally:
            with DATABASE_ENGINE.begin() as connection:
                connection.execute(
                    text("DELETE FROM domain WHERE domain = :domain"),
                    {"domain": domain_name},
                )


if __name__ == "__main__":
    unittest.main()
