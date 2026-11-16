# OpenSigWeave MariaDB Compatibility Audit

**Status:** Source-level audit completed; no runtime migration test has been run yet  
**Target database:** Mailcow MariaDB 10.11, using the existing `mysql-mailcow` service  
**Audited upstream:** `VeiglbergTech/OpenSigWeave:dev` at `93b360225c5008fb60f6598e431ee2e3ff03842d`

## Findings

### 1. Database driver

The standalone OpenSigWeave requirements currently include `psycopg[binary]`, but no MariaDB/MySQL driver. `database.py` accepts a generic SQLAlchemy URL, so the engine can use a MySQL dialect once a driver is added. A pinned pure-Python driver such as PyMySQL is the lowest-friction option for the Mailcow image.

The current PostgreSQL password validation is PostgreSQL-specific. Mailcow mode needs its own required database configuration validation without inheriting PostgreSQL-only environment variables.

### 2. Unbounded indexed strings

The SQLAlchemy models use unbounded `String()` for `domains.domain_name` and `user_overrides.user_email`, both of which are indexed/unique. The initial migration repeats those definitions. MySQL/MariaDB requires bounded lengths for VARCHAR columns and cannot safely create these indexes from an unbounded SQLAlchemy String declaration.

The Mailcow variant must use explicit bounded lengths for indexed values (for example, 255 for a domain name and 320 for an email address). Other unindexed unbounded text values should remain TEXT rather than being assigned arbitrary short limits.

### 3. Migration portability

Migration `0012_preserve_signature_html_source.py` uses `IS DISTINCT FROM` in its downgrade guard. That expression is not supported by MariaDB. The guard must be rewritten using a portable null-safe comparison before the migration chain is considered MariaDB-compatible.

The remaining migration files reviewed use ordinary table/index/foreign-key operations, booleans, timestamps and JSON columns. They still require an actual MariaDB 10.11 migration run; source inspection alone is not proof of compatibility.

### 4. Table isolation

The upstream models and migrations currently create generic names such as `domains`, `signatures`, `signature_rules` and `alembic_version`. These names must not be created in Mailcow's existing application database without isolation.

The Mailcow integration will use a reserved `osw_` namespace for every OpenSigWeave table and a separate `osw_alembic_version` table. Foreign-key targets, indexes, migration operations and model metadata must all use the same names. Mailcow's own tables must never be modified by OpenSigWeave migrations.

### 5. Runtime configuration

The upstream `migrations/env.py` loads `DATABASE_URL` and imports the standalone model metadata. The Mailcow runtime must provide a MariaDB URL through internal environment configuration, must not expose the database port to the host, and must run migrations only against the reserved OpenSigWeave namespace.

The engine will connect to the existing `mysql-mailcow` service on the private Compose network. This is one MariaDB server and one Compose deployment; no PostgreSQL or additional database container is required.

## Required implementation and test work

1. Add the MariaDB driver to the Mailcow engine image.
2. Add a Mailcow-specific persistence adapter/model namespace using `osw_` table names.
3. Make all foreign-key references and Alembic migration operations consistently use the prefixed names.
4. Set Alembic's version table to `osw_alembic_version`.
5. Bound indexed string columns and use TEXT for unbounded content.
6. Replace the MariaDB-incompatible downgrade comparison in the Mailcow migration set.
7. Add automated tests that compile model metadata with the MySQL/MariaDB dialect and assert that no unprefixed OpenSigWeave table is emitted.
8. Run the full Alembic upgrade chain against a clean MariaDB 10.11 instance, then test restart/idempotence and downgrade guards where supported.
9. Verify that Mailcow tables and existing data remain untouched.

## Limitations of this audit

This is a source-level compatibility audit only. No MariaDB container was started and no migration was executed. The integration must not be marked database-compatible until the automated and real MariaDB tests pass.
