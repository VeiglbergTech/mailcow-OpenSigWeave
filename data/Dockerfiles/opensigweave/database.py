"""Mailcow-backed database configuration for the OpenSigWeave engine.

The engine shares Mailcow's existing MariaDB service and database. All
OpenSigWeave tables are isolated by the osw_ prefix in models.py.
"""
import os

from sqlalchemy import URL, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


def database_url_from_env() -> URL:
    """Build a safe SQLAlchemy URL from Mailcow's existing DB credentials."""
    required = ("DBNAME", "DBUSER", "DBPASS")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            "Missing Mailcow database environment variables: " + ", ".join(missing)
        )

    try:
        port = int(os.environ.get("OPENSIGWEAVE_DB_PORT", "3306"))
    except ValueError as exc:
        raise RuntimeError("OPENSIGWEAVE_DB_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("OPENSIGWEAVE_DB_PORT must be between 1 and 65535")

    return URL.create(
        drivername="mysql+pymysql",
        username=os.environ["DBUSER"],
        password=os.environ["DBPASS"],
        host=os.environ.get("OPENSIGWEAVE_DB_HOST", "mysql"),
        port=port,
        database=os.environ["DBNAME"],
        query={"charset": "utf8mb4"},
    )


def create_database_engine():
    """Create the private engine connection to Mailcow's MariaDB service."""
    return create_engine(
        database_url_from_env(),
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_size=5,
        max_overflow=5,
    )


def create_session_factory(engine):
    """Return a session factory bound to the supplied engine."""
    return sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
