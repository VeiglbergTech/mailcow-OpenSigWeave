"""HTTP boundary smoke tests for the embedded engine."""
import base64
import os
import sys
import unittest
import tempfile
import uuid
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
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key-that-is-at-least-32-characters-long")
os.environ.setdefault("SIGNATURE_STORAGE_PATH", str(Path(tempfile.gettempdir()) / "opensigweave-engine-http-tests"))

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from app import app, SessionLocal
from models import DomainDB, SelectionStateDB


class EngineHttpBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        command.upgrade(Config(str(ENGINE_PATH / "alembic.ini")), "head")

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

    def test_rewrite_endpoint_requires_engine_key_and_places_signature_before_quote(self):
        raw = (
            b"From: alice@example.test\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"New answer\r\n\r\n> quoted answer\r\n"
        )
        payload = {
            "message_b64": base64.b64encode(raw).decode("ascii"),
            "html": "<p>Signature</p>",
            "plain": "Signature",
            "inject_on_replies": True,
        }
        self.assertEqual(self.client.post("/internal/rewrite-message", json=payload).status_code, 401)
        response = self.client.post(
            "/internal/rewrite-message",
            headers={"X-Engine-Key": os.environ["ENGINE_API_KEY"]},
            json=payload,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers.get("X-OpenSigWeave-Rewritten"), "1")
        self.assertLess(response.content.index(b"Signature"), response.content.index(b"> quoted answer"))

    def test_rewrite_endpoint_noops_signed_messages(self):
        raw = (
            b"From: alice@example.test\r\nMIME-Version: 1.0\r\n"
            b"Content-Type: multipart/signed; boundary=sig; protocol=application/pgp-signature\r\n\r\n"
            b"--sig\r\nContent-Type: text/plain\r\n\r\nSigned\r\n"
            b"--sig\r\nContent-Type: application/pgp-signature\r\n\r\nsig\r\n--sig--\r\n"
        )
        response = self.client.post(
            "/internal/rewrite-message",
            headers={"X-Engine-Key": os.environ["ENGINE_API_KEY"]},
            json={
                "message_b64": base64.b64encode(raw).decode("ascii"),
                "html": "<p>Signature</p>",
                "plain": "Signature",
                "inject_on_replies": True,
            },
        )
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(response.headers.get("X-OpenSigWeave-Rewrite-Reason"), "protected-or-malformed")

    def test_signature_request_can_defer_rotation_commit(self):
        payload = {
            "authenticated_user": "alice@example.test",
            "envelope_sender": "alice@example.test",
            "target_domain": "example.test",
            "profile": {},
            "commit_selection": False,
        }
        response = self.client.post(
            "/internal/signature",
            headers={"X-Engine-Key": os.environ["ENGINE_API_KEY"]},
            json=payload,
        )
        self.assertIn(response.status_code, {200, 400})

    def test_signature_endpoint_rejects_invalid_sender_after_authentication(self):
        response = self.client.post(
            "/internal/signature",
            headers={"X-Engine-Key": os.environ["ENGINE_API_KEY"]},
            json={"authenticated_user": "alice@example.test", "envelope_sender": "not-an-email", "target_domain": "example.test", "profile": {}},
        )
        self.assertEqual(response.status_code, 400)

    def test_private_admin_api_manages_domains_signatures_groups_and_rules(self):
        unauthorized = self.client.get("/admin/catalog")
        self.assertEqual(unauthorized.status_code, 401)

        headers = {"X-Admin-Key": os.environ["ADMIN_API_KEY"]}
        suffix = uuid.uuid4().hex[:12]
        domain_name = f"admin-{suffix}.example.test"
        domain = self.client.post("/admin/domains", headers=headers, json={"domain_name": domain_name})
        self.assertEqual(domain.status_code, 200, domain.text)
        domain_id = domain.json()["id"]
        domain_update = self.client.put("/admin/domains/" + str(domain_id), headers=headers, json={
            "domain_name": domain_name,
            "is_active": False,
            "allow_overrides": False,
            "inject_on_replies": True,
            "trim_whitespace": False,
            "strip_device_signatures": True,
        })
        self.assertEqual(domain_update.status_code, 200, domain_update.text)
        self.assertEqual(domain_update.json()["is_active"], False)
        self.assertEqual(domain_update.json()["allow_overrides"], False)
        self.assertEqual(domain_update.json()["inject_on_replies"], True)
        self.assertEqual(domain_update.json()["trim_whitespace"], False)
        self.assertEqual(domain_update.json()["strip_device_signatures"], True)

        signature = self.client.post("/admin/signatures", headers=headers, json={
            "name": f"Admin signature {suffix}",
            "html_content": "<p>Hello {{ first_name }}</p>",
            "mode": "AUTO",
            "domain_ids": [domain_id],
            "weight": 7,
        })
        self.assertEqual(signature.status_code, 200, signature.text)
        signature_id = signature.json()["id"]
        self.assertEqual(signature.json()["weight"], 7)
        self.assertEqual(signature.json()["version"], 1)

        preview = self.client.post("/admin/preview", headers=headers, json={
            "html_content": "<p>Hello {{ first_name }} at {{ domain_name }}</p>",
            "domain_id": domain_id,
        })
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertIn("Hello Max", preview.json()["html"])
        self.assertIn(domain_name, preview.json()["html"])

        signature_v2 = self.client.put("/admin/signatures/" + signature_id, headers=headers, json={
            "html_content": "<p>Second version {{ first_name }}</p>",
        })
        self.assertEqual(signature_v2.status_code, 200, signature_v2.text)
        self.assertEqual(signature_v2.json()["version"], 2)

        versions = self.client.get("/admin/signatures/" + signature_id + "/versions", headers=headers)
        self.assertEqual(versions.status_code, 200, versions.text)
        self.assertEqual([item["version_number"] for item in versions.json()["versions"]], [2, 1])
        original_version = next(item for item in versions.json()["versions"] if item["version_number"] == 1)

        restored = self.client.post(
            "/admin/signatures/" + signature_id + "/versions/" + original_version["id"] + "/restore",
            headers=headers,
            json={},
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["version_number"], 3)

        restored_catalog = self.client.get("/admin/catalog", headers=headers)
        restored_signature = next(item for item in restored_catalog.json()["signatures"] if item["id"] == signature_id)
        self.assertIn("Hello {{ first_name }}", restored_signature["html_content"])
        self.assertEqual(restored_signature["version"], 3)

        signature_update = self.client.put("/admin/signatures/" + signature_id, headers=headers, json={
            "is_active": False,
            "variant_enabled": False,
        })
        self.assertEqual(signature_update.status_code, 200, signature_update.text)
        self.assertFalse(signature_update.json()["is_active"])
        self.assertFalse(signature_update.json()["variant_enabled"])

        group = self.client.post("/admin/groups", headers=headers, json={
            "name": f"Admin group {suffix}",
            "domain_ids": [domain_id],
            "members": [{"signature_id": signature_id, "weight": 5, "is_enabled": True}],
        })
        self.assertEqual(group.status_code, 200, group.text)
        group_id = group.json()["id"]
        group_update = self.client.put("/admin/groups/" + group_id, headers=headers, json={
            "name": f"Admin group {suffix}",
            "domain_ids": [domain_id],
            "is_active": False,
            "members": [{"signature_id": signature_id, "weight": 11, "is_enabled": True}],
        })
        self.assertEqual(group_update.status_code, 200, group_update.text)

        rule = self.client.post("/admin/rules", headers=headers, json={
            "name": f"Admin rule {suffix}",
            "priority": 50,
            "action_mode": "GROUP",
            "group_id": group_id,
            "conditions": [
                {"kind": "domain_equals", "key": "", "value": domain_name},
                {"kind": "sender_equals", "key": "", "value": f"alice@{domain_name}"},
            ],
        })
        self.assertEqual(rule.status_code, 200, rule.text)
        rule_id = rule.json()["id"]
        rule_update = self.client.put("/admin/rules/" + rule_id, headers=headers, json={
            "name": f"Admin rule {suffix}",
            "priority": 75,
            "action_mode": "GROUP",
            "group_id": group_id,
            "is_enabled": False,
            "conditions": [
                {"kind": "domain_equals", "key": "", "value": domain_name},
                {"kind": "sender_equals", "key": "", "value": f"alice@{domain_name}"},
            ],
        })
        self.assertEqual(rule_update.status_code, 200, rule_update.text)
        blocked_domain_delete = self.client.delete("/admin/domains/" + str(domain_id), headers=headers)
        self.assertEqual(blocked_domain_delete.status_code, 409, blocked_domain_delete.text)

        groups = self.client.get("/admin/groups", headers=headers)
        self.assertEqual(groups.status_code, 200, groups.text)
        stored_group = next(item for item in groups.json() if item["id"] == group_id)
        self.assertEqual(stored_group["members"][0]["weight"], 11)
        self.assertFalse(stored_group["is_active"])
        rules = self.client.get("/admin/rules", headers=headers)
        self.assertEqual(rules.status_code, 200, rules.text)
        stored_rule = next(item for item in rules.json() if item["name"] == f"Admin rule {suffix}")
        self.assertEqual(stored_rule["priority"], 75)
        self.assertFalse(stored_rule["is_enabled"])
        self.assertEqual(len(stored_rule["conditions"]), 2)
        self.assertEqual({condition["kind"] for condition in stored_rule["conditions"]}, {"domain_equals", "sender_equals"})

        self.assertEqual(self.client.delete("/admin/rules/" + rule_id, headers=headers).status_code, 200)
        self.assertEqual(self.client.delete("/admin/groups/" + group_id, headers=headers).status_code, 200)
        self.assertEqual(self.client.delete("/admin/signatures/" + signature_id, headers=headers).status_code, 200)
        deleted_domain = self.client.delete("/admin/domains/" + str(domain_id), headers=headers)
        self.assertEqual(deleted_domain.status_code, 200, deleted_domain.text)

    def test_process_message_commits_rotation_only_after_successful_mime_rewrite(self):
        headers = {"X-Admin-Key": os.environ["ADMIN_API_KEY"]}
        suffix = uuid.uuid4().hex[:12]
        domain_name = f"rotation-{suffix}.example.test"
        sender = f"alice@{domain_name}"

        domain_response = self.client.post(
            "/admin/domains",
            headers=headers,
            json={"domain_name": domain_name},
        )
        self.assertEqual(domain_response.status_code, 200, domain_response.text)
        domain_id = domain_response.json()["id"]

        signature_ids = []
        try:
            for index in (1, 2):
                response = self.client.post(
                    "/admin/signatures",
                    headers=headers,
                    json={
                        "name": f"Rotation {suffix} {index}",
                        "html_content": f"<p>Signature {index}</p>",
                        "mode": "AUTO",
                        "domain_ids": [domain_id],
                        "weight": 1,
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                signature_ids.append(response.json()["id"])

            normal_raw = (
                b"From: alice@" + domain_name.encode("ascii") + b"\r\n"
                b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
                b"Hello\r\n"
            )
            normal = self.client.post(
                "/internal/process-message",
                headers={"X-Engine-Key": os.environ["ENGINE_API_KEY"]},
                json={
                    "message_b64": base64.b64encode(normal_raw).decode("ascii"),
                    "authenticated_user": sender,
                    "envelope_sender": sender,
                    "target_domain": domain_name,
                    "profile": {},
                },
            )
            self.assertEqual(normal.status_code, 200, normal.text)
            self.assertEqual(normal.headers.get("X-OpenSigWeave-Source"), "opensigweave")

            with SessionLocal() as session:
                state = session.query(SelectionStateDB).filter_by(
                    sender_email=sender,
                    pool_name="AUTO",
                ).one()
                committed_variant = state.last_variant_id
                self.assertIsNotNone(committed_variant)

            signed_raw = (
                b"From: alice@" + domain_name.encode("ascii") + b"\r\n"
                b"MIME-Version: 1.0\r\n"
                b"Content-Type: multipart/signed; boundary=sig; protocol=application/pgp-signature\r\n\r\n"
                b"--sig\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nSigned body\r\n"
                b"--sig\r\nContent-Type: application/pgp-signature\r\n\r\nsignature\r\n"
                b"--sig--\r\n"
            )
            signed = self.client.post(
                "/internal/process-message",
                headers={"X-Engine-Key": os.environ["ENGINE_API_KEY"]},
                json={
                    "message_b64": base64.b64encode(signed_raw).decode("ascii"),
                    "authenticated_user": sender,
                    "envelope_sender": sender,
                    "target_domain": domain_name,
                    "profile": {},
                },
            )
            self.assertEqual(signed.status_code, 204, signed.text)
            self.assertEqual(
                signed.headers.get("X-OpenSigWeave-Rewrite-Reason"),
                "protected-or-malformed",
            )

            with SessionLocal() as session:
                state = session.query(SelectionStateDB).filter_by(
                    sender_email=sender,
                    pool_name="AUTO",
                ).one()
                self.assertEqual(state.last_variant_id, committed_variant)
        finally:
            with SessionLocal() as session:
                session.query(SelectionStateDB).filter_by(
                    sender_email=sender,
                    pool_name="AUTO",
                ).delete(synchronize_session=False)
                session.commit()
            for signature_id in reversed(signature_ids):
                self.client.delete("/admin/signatures/" + signature_id, headers=headers)
            self.client.delete("/admin/domains/" + str(domain_id), headers=headers)

    def test_signature_endpoint_renders_using_mailcow_profile_and_domain(self):
        domain_name = f"engine-{os.getpid()}-{id(self)}.example.test"
        sender = f"alice@{domain_name}"
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
            self.assertIn("Ada", payload["plain"])
            self.assertIn("+49 123 456", payload["plain"])
            self.assertFalse(payload["inject_on_replies"])
        finally:
            with SessionLocal() as session:
                stored = session.query(DomainDB).filter_by(domain_name=domain_name).one_or_none()
                if stored is not None:
                    session.delete(stored)
                    session.commit()


if __name__ == "__main__":
    unittest.main()
