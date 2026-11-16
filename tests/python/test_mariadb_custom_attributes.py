"""Verify the MariaDB JSON_SET profile namespace merge used by PHP identity sync."""
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_PATH = REPO_ROOT / "data" / "Dockerfiles" / "opensigweave"
sys.path.insert(0, str(ENGINE_PATH))

from sqlalchemy import text
from database import create_database_engine


@unittest.skipUnless(os.environ.get("OPENSIGWEAVE_DB_HOST"), "MariaDB integration service is not configured")
class MariaDBMailboxProfileNamespaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_database_engine()

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def test_profile_sync_preserves_unrelated_custom_attributes_and_replaces_its_namespace(self):
        with self.engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE osw_test_custom_attributes ("
                "username VARCHAR(254) PRIMARY KEY, custom_attributes LONGTEXT NULL)"
            ))
            try:
                connection.execute(text(
                    "INSERT INTO osw_test_custom_attributes "
                    "(username, custom_attributes) VALUES "
                    "('alice@example.test', '{\"theme\":\"blue\",\"opensigweave\":{\"phone\":\"old\"}}')"
                ))
                connection.execute(text(
                    """UPDATE osw_test_custom_attributes
                       SET custom_attributes = JSON_SET(
                         CASE
                           WHEN JSON_VALID(custom_attributes) = 1 AND JSON_TYPE(custom_attributes) = 'OBJECT'
                           THEN custom_attributes
                           ELSE JSON_OBJECT()
                         END,
                         '$.opensigweave',
                         JSON_EXTRACT(:profile, '$')
                       )
                       WHERE username = :username"""
                ), {
                    "profile": '{"principal_id":"ldap:guid-1","phone":"+49 123","attributes":{"department":"Research"}}',
                    "username": "alice@example.test",
                })
                row = connection.execute(text(
                    "SELECT JSON_UNQUOTE(JSON_EXTRACT(custom_attributes, '$.theme')) AS theme, "
                    "JSON_UNQUOTE(JSON_EXTRACT(custom_attributes, '$.opensigweave.phone')) AS phone, "
                    "JSON_EXTRACT(custom_attributes, '$.opensigweave.old') AS old_phone "
                    "FROM osw_test_custom_attributes WHERE username = 'alice@example.test'"
                )).mappings().one()
                self.assertEqual(row["theme"], "blue")
                self.assertEqual(row["phone"], "+49 123")
                self.assertIsNone(row["old_phone"])
            finally:
                connection.execute(text("DROP TABLE IF EXISTS osw_test_custom_attributes"))


if __name__ == "__main__":
    unittest.main()
