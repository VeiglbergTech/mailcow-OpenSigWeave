# OpenSigWeave engine for Mailcow

This directory contains the Mailcow-mode engine components. They are built and
deployed as part of this repository's Mailcow Compose project; no separate
OpenSigWeave checkout or database service is required.

## Database boundary

- Connects to Mailcow's existing MariaDB service (mysql by default).
- Uses Mailcow's existing DBNAME, DBUSER and DBPASS credentials.
- Does not create a second database container or PostgreSQL dependency.
- Every application table uses the reserved osw_ prefix.
- Alembic uses osw_alembic_version, not Mailcow's or standalone OpenSigWeave's
  generic alembic_version.
- The model set deliberately excludes standalone LDAP/OIDC provider metadata
  and identity caches. Mailcow will supply the authenticated mailbox principal
  and allowlisted profile attributes to the headless engine.

Optional connection overrides:

- OPENSIGWEAVE_DB_HOST (default: mysql)
- OPENSIGWEAVE_DB_PORT (default: 3306)

## Current scope and limitations

The current milestone provides the MariaDB connection factory, isolated
SQLAlchemy models and initial Alembic schema migration. It does not yet provide
the HTTP engine, Compose service, PHP adapter, native Mailcow management UI,
identity-sync extension or signature backup integration.

The migration currently uses SQLAlchemy metadata for the initial schema.
Future schema changes must be added as explicit forward Alembic revisions.
Do not run this migration against a live Mailcow database until the complete
engine integration and upgrade/backup procedure have been reviewed.
