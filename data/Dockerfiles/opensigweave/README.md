# OpenSigWeave engine for Mailcow

This directory contains the embedded, headless OpenSigWeave engine. It is
built from this repository and deployed as part of Mailcow's Compose project;
a separate OpenSigWeave checkout or database service is not required.

## Database boundary

- Uses Mailcow's existing MariaDB service and DBNAME, DBUSER and DBPASS.
- All application tables use the reserved `osw_` prefix.
- Alembic uses `osw_alembic_version`.
- No standalone LDAP/OIDC provider registry or identity cache is included.
- Signature HTML and immutable snapshots are stored below
  `SIGNATURE_STORAGE_PATH` (default `/var/lib/opensigweave`).

Optional database overrides:

- `OPENSIGWEAVE_DB_HOST` (default: `mysql`)
- `OPENSIGWEAVE_DB_PORT` (default: `3306`)

## Private engine API

The service exposes only:

- `GET /health`: liveness endpoint; no database query.
- `POST /internal/signature`: signature compilation, protected by
  `X-Engine-Key`.

The request distinguishes three Mailcow values:

- `authenticated_user`: authenticated mailbox identity, used for profile
  lookup, overrides and per-user selection state.
- `envelope_sender`: actual SMTP envelope sender, used for sender rules and
  the rendered `email` template value.
- `target_domain`: Mailcow-resolved primary domain, including alias-domain
  resolution.

The caller must be the trusted Mailcow PHP footer adapter. The engine does not
authenticate SMTP users itself and does not connect to LDAP, Active Directory,
OIDC or Authentik. Profile values are passed by Mailcow from its own mailbox
metadata. The API key must be generated and shared only between the PHP adapter
and this private service before Compose integration is enabled.

## Image

The Dockerfile builds a non-root runtime. Its entrypoint initializes ownership
of the persistent signature volume before dropping privileges. Alembic applies
the namespaced schema before Uvicorn starts.

## Current scope and limitations

Implemented:

- MariaDB connection and isolated schema models.
- Initial Alembic migration and MariaDB 10.11 integration tests.
- Mailcow-mode selection and rule repositories, including MariaDB upsert
  support for no-repeat variant and group rotation.
- Sandboxed Jinja rendering, HTML allowlist sanitization and hardened
  versioned signature-file storage.
- Private headless HTTP engine and request-boundary tests.
- Non-root Docker image and persistent-storage entrypoint.

Not implemented yet:

- Compose service and shared secret provisioning.
- PHP adapter in Mailcow's existing `footer.php` pipeline.
- Mapping from `mailbox.custom_attributes` to the engine profile contract.
- Extension of the existing Mailcow LDAP identity sync.
- Native Mailcow management pages and role authorization.
- Signature-volume backup/restore integration.
- End-to-end Rspamd, SMTP, MIME and DKIM tests.

The current GitHub Actions suite uses a disposable MariaDB 10.11 service. It
validates the engine's schema, MariaDB selection repositories and private HTTP
boundary. It does not validate a complete Mailcow deployment or production
mail flow. Do not enable this service on a live Mailcow installation yet.
