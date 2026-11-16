from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from database import Base, database_url_from_env
import models  # noqa: F401 - register Mailcow-mode metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
VERSION_TABLE = "osw_alembic_version"


def run_migrations_offline() -> None:
    context.configure(
        url=str(database_url_from_env()),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        version_table=VERSION_TABLE,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(
        database_url_from_env(),
        poolclass=pool.NullPool,
        pool_pre_ping=True,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            version_table=VERSION_TABLE,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
