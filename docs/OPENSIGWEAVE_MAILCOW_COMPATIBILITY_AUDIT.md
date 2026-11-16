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

## Existing Mailcow footer pipeline (important discovery)

Mailcow already has a native domain-wide footer implementation:

- The `domain_wide_footer` table stores HTML, plain text, mailbox exclusions, alias-domain exclusions and the reply-skip setting.
- `data/conf/rspamd/lua/rspamd.local.lua` registers the `MOO_FOOTER` prefilter.
- `MOO_FOOTER` calls the internal `http://nginx:8081/footer.php` endpoint, passing the authenticated SMTP username, envelope sender and sender domain.
- `data/conf/rspamd/dynmaps/footer.php` reads the existing footer and mailbox custom attributes from MariaDB and returns JSON.
- The Lua callback uses Rspamd's `lua_mime.add_text_footer` to update the MIME body and relevant content headers.

This is a better integration point than installing a second independent Rspamd injection plugin. The Mailcow distribution should investigate extending the existing footer data provider so it can return an OpenSigWeave-selected signature when OpenSigWeave is enabled for the domain, while preserving the current `domain_wide_footer` behavior as the fallback. This avoids two competing MIME injectors and reuses Mailcow's established SMTP identity and MIME handling path.

The standalone OpenSigWeave Lua adapter should therefore not be copied into Mailcow by default. Its selection and marker semantics can be retained only where they can be integrated safely into the existing `MOO_FOOTER` flow. The existing footer endpoint provides both authenticated username and envelope sender; any engine adapter must preserve that distinction and must not assume the envelope sender is the authenticated mailbox.

## Proposed technical direction

Retain the existing Python selection/rendering/sanitization engine, but separate it from the standalone portal. Keep the native Mailcow PHP frontend responsible for browser sessions, CSRF, permissions and UI. Keep the engine on the Mailcow Docker network with no published host port.

Prefer adapting Mailcow's existing `footer.php` / `MOO_FOOTER` path over adding another Rspamd Lua injection plugin. Use Mailcow's existing authenticated SMTP username, envelope sender, alias-domain resolution, mailbox exclusions and MIME rewriting as the integration boundary. OpenSigWeave should provide the selected signature data to that pipeline only when enabled; otherwise the current Mailcow footer must continue unchanged.

Before connecting the engine to MariaDB, implement a dedicated persistence namespace and prove the complete migration chain against MariaDB 10.11. Preserve the existing filesystem version store and add it to Mailcow's normal backup/restore path unless testing demonstrates that storing HTML in MariaDB is safer and simpler.

Do not enable message injection until the integration tests prove authenticated SMTP identity, envelope-sender authorization, alias-domain behavior, MIME handling, reply handling, existing-footer fallback and final DKIM ordering.

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


## 6. User profile data and existing directory connection

This is a separate requirement from mailbox authentication and must not introduce another LDAP/Active Directory connection.

Mailcow already provides the required data path for signatures:

- `mailbox.custom_attributes` is a JSON column.
- `data/conf/rspamd/dynmaps/footer.php` reads the authenticated mailbox's `custom_attributes` and returns them as the `vars` object.
- The existing Mailcow footer pipeline therefore already transports mailbox-specific values to the signature rendering stage.

The current Mailcow identity sync does **not** currently import a general set of profile attributes such as `telephoneNumber` into `mailbox.custom_attributes`. The LDAP directory reader currently requests the configured username field, `displayName`, `cn`, `userAccountControl`, one optional `attributeField` (used for mailbox-template selection), stable-ID attributes and optionally `memberOf`. The sync planner currently applies the display name and mailbox template; it does not synchronize arbitrary LDAP profile fields.

The Mailcow schema also contains `c_telephonenumber` in SOGo-related address-book/static-view tables. That alone is **not** evidence that a mailbox's AD telephone number is available to the footer pipeline.

### Required design

Extend Mailcow's existing identity-sync flow, rather than adding another connection:

1. Reuse the already configured LDAP provider settings and the same `identity_provider('init')` connection created for the existing identity sync.
2. Add an explicit allowlisted LDAP attribute mapping for the profile fields that should be synchronized, e.g. `telephoneNumber`, `department`, `title`, `givenName`, `sn`, and optionally `mobile`.
3. Request those attributes in the same paged LDAP user search; do not open a second LDAP bind/session or add another service.
4. Persist the mapped values in the existing `mailbox.custom_attributes` JSON under a reserved OpenSigWeave-owned namespace, preserving unrelated Mailcow custom attributes and any manually managed values.
5. Have the existing `footer.php` response pass those values through to OpenSigWeave. No LDAP connection is made by OpenSigWeave at send time.
6. Define clearing behavior for attributes removed from AD, missing values, and disabled/removed users. Only keys owned by the directory mapping may be automatically overwritten or cleared.

The exact public placeholder names and whether manual per-mailbox overrides take precedence over directory values remain product decisions. They must be explicit and documented before implementation.

**Conclusion:** the telephone number can be sourced from AD without a new connection, but the current Mailcow sync does not yet copy it into the mailbox footer variables. A small extension to the existing Mailcow identity sync is required.

## OpenSigWeave engine identity dependency

The upstream signature endpoint is currently `GET /api/signature/{email}`. It validates `X-Engine-Key`, but then obtains profile attributes through `get_identity_attributes()`. That function can call the standalone OpenSigWeave identity-provider registry (OIDC/LDAP) and its cache. The FastAPI startup also contains a migration path for legacy standalone identity-provider environment settings.

This behavior must not be carried into Mailcow mode. The Mailcow adapter needs a separate authenticated request contract that passes a bounded allowlisted profile payload sourced by Mailcow from `mailbox.custom_attributes`. The engine must construct its identity context from that payload and a stable Mailcow mailbox principal, and must not invoke the standalone provider registry, cache, LDAP or OIDC provider code. The standalone endpoint may remain available in the original OpenSigWeave project; the Mailcow build must expose only its headless, Mailcow-specific API and health endpoint.

This is a source-level finding from the current upstream `main.py`; it is not yet implemented.
