"""Real MariaDB 10.11 migration smoke test (enabled in CI)."""
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_PATH = REPO_ROOT / "data" / "Dockerfiles" / "opensigweave"
sys.path.insert(0, str(ENGINE_PATH))

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from database import create_database_engine


@unittest.skipUnless(
    os.environ.get("OPENSIGWEAVE_DB_HOST"),
    "MariaDB integration service is not configured",
)
class OpenSigWeaveMariaDBMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_database_engine()
        cls.alembic_config = Config(str(ENGINE_PATH / "alembic.ini"))
        command.upgrade(cls.alembic_config, "head")

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def test_application_tables_and_version_table_exist(self):
        inspector = inspect(self.engine)
        table_names = set(inspector.get_table_names())
        self.assertIn("osw_alembic_version", table_names)
        application_tables = {name for name in table_names if name.startswith("osw_")}
        self.assertGreaterEqual(len(application_tables), 14)
        self.assertEqual(application_tables, table_names)

    def test_foreign_keys_reference_only_osw_tables(self):
        inspector = inspect(self.engine)
        for table_name in inspector.get_table_names():
            if not table_name.startswith("osw_"):
                continue
            for foreign_key in inspector.get_foreign_keys(table_name):
                self.assertTrue(
                    foreign_key["referred_table"].startswith("osw_"),
                    f"{table_name} references {foreign_key['referred_table']}",
                )

    def test_alembic_revision_is_recorded_and_upgrade_is_idempotent(self):
        with self.engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM osw_alembic_version")
            ).scalar_one()
        self.assertEqual(revision, "0001_mailcow_schema")
        command.upgrade(self.alembic_config, "head")


if __name__ == "__main__":
    unittest.main()
