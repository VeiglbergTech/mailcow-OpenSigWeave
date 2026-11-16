"""Static MariaDB-dialect checks for the embedded OpenSigWeave schema."""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_PATH = REPO_ROOT / "data" / "Dockerfiles" / "opensigweave"
sys.path.insert(0, str(ENGINE_PATH))

from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex, CreateTable

from database import Base
import models  # noqa: F401 - register metadata


class OpenSigWeaveMariaDBSchemaTests(unittest.TestCase):
    def test_all_tables_use_reserved_prefix(self):
        self.assertTrue(Base.metadata.tables)
        for table_name in Base.metadata.tables:
            self.assertTrue(
                table_name.startswith("osw_"),
                f"Unprefixed OpenSigWeave table: {table_name}",
            )

    def test_foreign_keys_stay_inside_reserved_namespace(self):
        for table in Base.metadata.tables.values():
            for foreign_key in table.foreign_keys:
                self.assertTrue(
                    foreign_key.target_fullname.startswith("osw_"),
                    f"Foreign key escapes OpenSigWeave namespace: "
                    f"{table.name}.{foreign_key.parent.name}",
                )

    def test_string_columns_have_bounded_lengths(self):
        for table in Base.metadata.tables.values():
            for column in table.columns:
                if isinstance(column.type, mysql.VARCHAR) or (
                    getattr(column.type, "python_type", None) is str
                    and hasattr(column.type, "length")
                ):
                    self.assertIsNotNone(
                        column.type.length,
                        f"Unbounded string column: {table.name}.{column.name}",
                    )

    def test_schema_compiles_for_mysql_dialect(self):
        dialect = mysql.dialect()
        for table in Base.metadata.sorted_tables:
            statement = str(CreateTable(table).compile(dialect=dialect))
            self.assertIn(f"CREATE TABLE {table.name}", statement)
            for index in table.indexes:
                str(CreateIndex(index).compile(dialect=dialect))

    def test_standalone_identity_tables_are_not_present(self):
        self.assertNotIn("identity_providers", Base.metadata.tables)
        self.assertNotIn("identity_attribute_cache", Base.metadata.tables)
        self.assertNotIn("osw_identity_providers", Base.metadata.tables)
        self.assertNotIn("osw_identity_attribute_cache", Base.metadata.tables)


if __name__ == "__main__":
    unittest.main()
