# OpenSigWeave / Mailcow Compatibility Audit

**Status:** Source audit completed; implementation blockers remain  
**Mailcow target:** MariaDB 10.11 in the current Compose file  
**OpenSigWeave source:** `VeiglbergTech/OpenSigWeave:dev`

## Executive result

The existing OpenSigWeave domain logic is reusable. The current standalone application is not yet safe to drop into Mailcow as-is. Its HTTP engine endpoint and Rspamd client already exist, but its database driver, schema/migrations, application startup and transport assumptions do not yet match Mailcow.

No application code has been imported into this repository. This audit is based on the current source trees and file contents, not on a successful runtime test.

## Reusable OpenSigWeave components

The following source modules are good candidates to retain:

- `signature_catalog.py`: transactional signature metadata and version operations.
- `signature_storage.py`: name-based `signatur/{name}/signature.html` storage, immutable version snapshots, SHA-256 integrity checks and safe filesystem operations.
- `signature_renderer.py` and `signature_sanitizer.py`: template rendering and HTML sanitization.
- `core/rules.py`, `core/rule_repository.py`, `core/selection_repository.py`, `core/group_selection_repository.py` and `core/variant_selector.py`: rule evaluation and deterministic/weighted signature selection.
- `rspamd/opensigweave.lua`: existing Rspamd MIME-aware signature injection adapter.

The source also already exposes `GET /api/signature/{email}`. The endpoint checks the `X-Engine-Key` header using a constant-time comparison and returns rendered HTML plus reply/whitespace/device-signature policy flags. The Lua adapter sends this header and consumes that endpoint.

## Confirmed integration blockers

### 1. Database driver and schema compatibility

Mailcow's current Compose file uses MariaDB 10.11. The OpenSigWeave requirements currently include `psycopg[binary]` but no MySQL/MariaDB DBAPI driver such as PyMySQL. `database.py` defaults to SQLite and only contains explicit password consistency checks for PostgreSQL URLs.

Adding a driver alone is not sufficient:

- The initial migrations create unique indexes for `domains.domain_name` and `user_overrides.user_email`, while those columns are declared as unbounded SQLAlchemy `String()` values. The MariaDB dialect needs a deliberate bounded/indexable column definition here.
- The current model and migration set has not been executed against MariaDB 10.11.
- Migrations use batch table alterations, raw SQL and JSON columns. Their exact MariaDB behavior must be tested rather than inferred from SQLite/PostgreSQL success.
- Migration `0012_preserve_signature_html_source.py` uses `IS DISTINCT FROM` in its downgrade guard. This syntax must be checked against the target MariaDB version or replaced with a portable null-safe comparison.
- SQLAlchemy's default Alembic version table and the current unprefixed model table names would share the Mailcow database namespace. The integration needs an explicit OpenSigWeave table prefix (including the Alembic version table), or another isolated schema/database arrangement within the existing MariaDB server.

**Decision gate:** do not point the standalone migrations at Mailcow's live database until a clean MariaDB 10.11 migration test and upgrade/downgrade test pass.

### 2. Standalone web application and session startup

The current `main.py` builds the standalone portal as well as the engine API. It configures Jinja/static assets, standalone session and CSRF middleware, login routes and standalone identity-provider management. Startup requires both `SECRET_KEY` and `ENGINE_API_KEY`.

The Mailcow distribution must not expose or depend on the standalone login/portal. A small configuration toggle is not automatically sufficient: route registration and middleware need to be separated cleanly so that a headless engine only exposes the explicitly required internal API.

The current OpenSigWeave Dockerfile starts Alembic and Uvicorn for the complete standalone application. It is not yet a Mailcow-ready engine image.

### 3. Rspamd transport and configuration loading

The existing Lua adapter currently requires an `https://` API URL and explicitly keeps TLS certificate verification enabled (`no_ssl_verify = false`). A Compose service on Mailcow's private Docker network would normally be addressed through service DNS, but plain `http://opensigweave:8085` is rejected by the current Lua configuration check.

Do not solve this by globally disabling TLS verification. Choose and test one of these approaches:

1. provide an internal HTTPS endpoint with a certificate trusted by the Rspamd container; or
2. define a narrowly scoped private-network transport policy, keep the engine un-published, restrict network membership, and retain the shared engine key.

Mailcow mounts `./data/conf/rspamd/plugins.d/` into Rspamd and mounts `./data/hooks/rspamd/` as its hook directory. The Rspamd entrypoint creates and prepares `plugins.d`, then executes executable hook files. Current Rspamd documentation confirms that custom Lua plugins belong in `/etc/rspamd/plugins.d/` and are enabled by a matching top-level configuration section. The existing module reads its settings from `rspamd_config:get_all_opt("opensigweave")`, so the intended integration is a plugin file in Mailcow's `data/conf/rspamd/plugins.d/` plus an `opensigweave { ... }` section in `data/conf/rspamd/rspamd.conf.local`. This still needs `rspamadm configtest` and a live Rspamd startup test in the target image. See the [Rspamd module documentation](https://docs.rspamd.com/getting-started/understanding-rspamd/) and [Lua plugin configuration guidance](https://docs.rspamd.com/guides/configuration/tool-selection/).

### 4. Native Mailcow UI and authorization

The Mailcow PHP bootstrap already initializes the authenticated PDO connection, Redis, sessions and authentication helpers. The active session exposes `mailcow_cc_username` and `mailcow_cc_role`; the known roles are `admin`, `domainadmin` and `user`. The native admin dashboard demonstrates the expected page pattern: load prerequisites, load the matching role trigger, call `protect_route(['admin'])`, then render through the common header/footer and Twig template.

The admin settings UI is a native Twig tab layout (`data/web/templates/admin.twig`). The configurable app-link mechanism in the header only creates links to other applications; it is not a replacement for a native Mailcow page. A native implementation should therefore add a proper Mailcow PHP route/template and only then add its navigation entry in the appropriate role-specific UI.

The PHP layer must enforce authorization on every request, not only hide navigation links. A role/domain/mailbox permission matrix is still required before implementation. At minimum, global configuration belongs to `admin`; `domainadmin` must be limited to domains assigned to that account; `user` must be limited to their own mailbox/personal override if that feature is enabled. These are proposed boundaries, not yet a completed implementation. The Python engine must never trust browser-provided identity or role headers; the PHP server must derive identity from the active Mailcow session and authenticate its server-to-server engine requests.

### 5. Backup and restore

The existing backup script validates a fixed set of backup targets and explicitly handles each archive during backup and restore. A new signature volume is not covered merely by adding it to Compose.

If signature assets remain filesystem-backed, the integration must update all of the following together:

- accepted backup target values and help/validation text;
- the `all` backup path;
- explicit backup archive creation;
- restore target validation and restore extraction;
- backup/restore regression tests and documentation.

The existing MariaDB physical backup covers the MariaDB data volume, so metadata tables in that same server remain within the existing database backup. The signature files still need their own archive if kept outside MariaDB.

## Proposed technical direction

Retain the existing Python selection/rendering/sanitization engine, but separate it from the standalone portal. Keep the native Mailcow PHP frontend responsible for browser sessions, CSRF, permissions and UI. Keep the engine on the Mailcow Docker network with no published host port.

Before connecting it to MariaDB, implement a dedicated persistence namespace and prove the complete migration chain against MariaDB 10.11. Preserve the existing filesystem version store and add it to Mailcow's normal backup/restore path unless testing demonstrates that storing HTML in MariaDB is safer and simpler.

Do not enable message injection until the integration tests prove authenticated SMTP identity, envelope-sender authorization, MIME behavior, reply handling and final DKIM ordering.

## Required implementation gates

1. Define and test the OpenSigWeave MariaDB schema/table prefix and version-table name.
2. Add the MariaDB driver and pass clean-install, upgrade, data-integrity and rollback tests against MariaDB 10.11.
3. Extract a headless engine application with only the required authenticated API routes.
4. Add a private Compose service with no published port and health/readiness checks.
5. Prove the Rspamd Lua module loads from Mailcow's supported configuration mechanism and can reach the engine with TLS/transport verification intact.
6. Define the Mailcow admin/domain-admin/mailbox-user permission matrix and implement server-side checks.
7. Extend backup/restore and regression tests for signature assets.
8. Add PHP lint, Python unit, MariaDB integration, Compose and Rspamd configuration checks to CI.

## Current state

This repository still contains only the Mailcow upstream baseline plus integration documentation. No runtime service, PHP page, migration, Rspamd hook or backup change has been committed yet.
