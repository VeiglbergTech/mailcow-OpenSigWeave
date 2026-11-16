# OpenSigWeave engine for Mailcow

This directory contains the embedded, headless OpenSigWeave engine. It is
built from this repository and deployed as part of Mailcow's Compose project;
a separate OpenSigWeave checkout or database service is not required.

## Database and storage

- Uses Mailcow's existing MariaDB service and DBNAME, DBUSER and DBPASS.
- All application tables use the reserved `osw_` prefix.
- Alembic uses `osw_alembic_version`.
- No standalone LDAP/OIDC provider registry or identity cache is included.
- Signature HTML and immutable snapshots are stored below
  `SIGNATURE_STORAGE_PATH` (default `/var/lib/opensigweave`).
- The persistent signature volume is included in Mailcow backup and restore.

Optional database overrides:

- `OPENSIGWEAVE_DB_HOST` (default: `mysql`)
- `OPENSIGWEAVE_DB_PORT` (default: `3306`)

## Private engine API

The Compose service is named `opensigweave-mailcow`, has no published host
port and is reachable on the private Mailcow network as `opensigweave:8090`.

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

The caller is Mailcow's existing PHP footer adapter. The engine does not
authenticate SMTP users itself and does not connect to LDAP, Active Directory,
OIDC or Authentik. Profile values are passed by Mailcow from its own mailbox metadata. The existing
LDAP identity-sync job now requests an explicit allowlist
(`displayName`, `givenName`, `sn`, `title`, `telephoneNumber`, `mobile`,
`department`) in the same paged LDAP search and persists the normalized values
under `mailbox.custom_attributes.opensigweave`. It does not create another LDAP
connection or bind. The existing footer reads this namespace at send time.

## Secret provisioning and activation

- New installations receive a random `OPENSIGWEAVE_ENGINE_KEY` from
  `generate_config.sh`.
- Existing installations receive one automatically from `update.sh` if it
  is missing; an existing non-empty value is preserved.
- The key is passed only to PHP-FPM and the private engine service.
- Signature injection remains disabled by default. Set
  `OPENSIGWEAVE_ENABLED=y` in `mailcow.conf` only after the remaining
  operational and SMTP/DKIM checks are complete.

## Upstream updates

The provider-neutral source baseline is pinned in `UPSTREAM_COMMIT`. Use `bash scripts/sync-opensigweave-core.sh` to refresh the curated modules from a reviewed upstream commit. The MariaDB-specific selection repositories are deliberately merged manually. See [the upstream sync procedure](../../../docs/OPENSIGWEAVE_UPSTREAM_SYNC.md).

## Image

The Dockerfile builds a non-root runtime. Its entrypoint initializes ownership
of the persistent signature volume before dropping privileges. Alembic applies
the namespaced schema before Uvicorn starts.

## Current implementation state

Implemented:

- MariaDB connection and isolated schema models.
- Initial Alembic migration and MariaDB 10.11 integration tests.
- Mailcow-mode rule and selection repositories, including MariaDB upsert
  support for no-repeat variant and group rotation.
- Sandboxed Jinja rendering, HTML allowlist sanitization and hardened
  versioned signature-file storage.
- Private headless HTTP engine with a request contract that keeps the
  authenticated mailbox distinct from the SMTP envelope sender and target
  domain.
- Private Compose service with no published port.
- Existing LDAP identity sync extended to map allowlisted AD profile fields into
  the reserved OpenSigWeave custom-attributes namespace; unrelated
  `custom_attributes` keys are preserved. The reserved namespace is sync-owned:
  absent AD values clear on the next successful sync.
- Dedicated engine-key provisioning for new and existing installations.
- PHP adapter in Mailcow's existing footer endpoint. It keeps the native
  Mailcow footer as fallback and does not add a second Rspamd MIME injector.
- Signature volume backup/restore support.

Still open:

- Native Mailcow management page with admin-only session authorization, a CSRF-protected PHP gateway, domain and signature management, CKEditor 5, weighted variants, and group/rule creation, editing and deletion UI.
- Full editing workflows for groups and rules, per-member weight editing, domain-admin delegation and dedicated management regression tests remain open.
- Rspamd integration tests for reply handling, plain/HTML and multipart MIME.
- Verification of trim-whitespace/device-signature policies and final DKIM
  behavior in a real Mailcow test installation.

GitHub Actions validates the Python schema, MariaDB selection repositories,
HTTP boundary, Docker image build, PHP syntax and shell syntax, the Mailcow
PHP identity-sync unit suite, and the Compose configuration. It does not
yet validate a complete Mailcow deployment or production mail flow. Signature
injection remains disabled by default; do not enable it before the remaining
operational and SMTP/DKIM tests pass.
