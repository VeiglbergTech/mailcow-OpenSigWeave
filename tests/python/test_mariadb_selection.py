"""Exercise selection repositories on the MariaDB dialect in CI."""
import os
import sys
import unittest
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_PATH = REPO_ROOT / "data" / "Dockerfiles" / "opensigweave"
sys.path.insert(0, str(ENGINE_PATH))

from sqlalchemy import delete
from sqlalchemy.orm import Session

from database import create_database_engine
from models import (
    DomainDB, GroupSelectionStateDB, SelectionStateDB, SignatureDB,
    SignatureGroupDB, SignatureGroupMemberDB, SignatureVariantDB,
)
from core.selection_repository import select_and_record_variant
from core.group_selection_repository import select_and_record_group_signature


@unittest.skipUnless(os.environ.get("OPENSIGWEAVE_DB_HOST"), "MariaDB integration service is not configured")
class MariaDBSelectionRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_database_engine()

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def test_variant_rotation_uses_mariadb_upsert(self):
        suffix = uuid.uuid4().hex[:12]
        domain_name = f"{suffix}.example.test"
        sender = f"sender-{suffix}@{domain_name}"
        with Session(self.engine) as session:
            domain = DomainDB(domain_name=domain_name)
            session.add(domain)
            session.flush()
            signatures = [
                SignatureDB(id=uuid.uuid4().hex, name=f"sig-{suffix}-{i}", domain_id=domain.id, scope="DOMAIN", mode="AUTO", is_active=True)
                for i in range(2)
            ]
            session.add_all(signatures)
            session.flush()
            variants = [
                SignatureVariantDB(id=uuid.uuid4().hex, signature_id=sig.id, pool_name="AUTO", weight=1, is_enabled=True)
                for sig in signatures
            ]
            session.add_all(variants)
            session.flush()

            first = select_and_record_variant(session, sender, "AUTO", domain.id)
            session.commit()
            second = select_and_record_variant(session, sender, "AUTO", domain.id)
            session.commit()

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertNotEqual(first.id, second.id)
            state = session.query(SelectionStateDB).filter_by(sender_email=sender, pool_name="AUTO").one()
            self.assertEqual(state.last_variant_id, second.id)

            session.delete(domain)
            session.commit()

    def test_group_rotation_uses_mariadb_upsert(self):
        suffix = uuid.uuid4().hex[:12]
        domain_name = f"{suffix}.example.test"
        principal = f"mailcow-directory:{uuid.uuid4().hex}"
        with Session(self.engine) as session:
            domain = DomainDB(domain_name=domain_name)
            group = SignatureGroupDB(id=uuid.uuid4().hex, name=f"group-{suffix}", slug=f"group-{suffix}")
            session.add_all([domain, group])
            session.flush()
            signatures = [
                SignatureDB(id=uuid.uuid4().hex, name=f"group-sig-{suffix}-{i}", domain_id=domain.id, scope="DOMAIN", mode="AUTO", is_active=True)
                for i in range(2)
            ]
            session.add_all(signatures)
            session.flush()
            session.add_all([
                SignatureGroupMemberDB(group_id=group.id, signature_id=sig.id, weight=1, is_enabled=True)
                for sig in signatures
            ])
            session.flush()

            first = select_and_record_group_signature(session, principal, group.id, domain.id)
            session.commit()
            second = select_and_record_group_signature(session, principal, group.id, domain.id)
            session.commit()

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertNotEqual(first.id, second.id)
            state = session.query(GroupSelectionStateDB).filter_by(principal_id=principal, group_id=group.id).one()
            self.assertEqual(state.last_signature_id, second.id)

            session.delete(domain)
            session.delete(group)
            session.commit()


if __name__ == "__main__":
    unittest.main()
