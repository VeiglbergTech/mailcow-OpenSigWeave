# OpenSigWeave Mailcow Integration

**Status:** Architecture work in progress  
**Target:** Mailcow-native signature management, developed in this Mailcow fork  
**Upstream:** `mailcow/mailcow-dockerized`  
**OpenSigWeave source:** `VeiglbergTech/OpenSigWeave`, branch `dev`

## Product boundary

This repository is the Mailcow-native distribution of OpenSigWeave. The standalone OpenSigWeave portal is not deployed here. Mailcow is the application shell, identity authority, database platform, deployment mechanism and backup system.

The integration must remain a reviewable set of changes on top of the Mailcow upstream so that it can be proposed as a pull request to `mailcow/mailcow-dockerized`.

## Explicit goals

- Manage signatures from the existing Mailcow web interface; no separately exposed OpenSigWeave portal.
- Reuse the authenticated Mailcow session and Mailcow role/domain/mailbox permissions. Do not create a second login system.
- Integrate navigation and page layout with Mailcow's existing UI conventions.
- Do not add a second PostgreSQL server or a separate backup/retention manager.
- Persist metadata in Mailcow's existing MariaDB service, using a dedicated OpenSigWeave table namespace and migrations.
- Store signature HTML and immutable version files under a Mailcow-managed data path that is included in the normal Mailcow backup and restore workflow.
- Reuse the OpenSigWeave signature selection, variants, rules, sanitization and rendering behavior where feasible.
- Keep mail-flow integration within Mailcow's existing Rspamd/Postfix architecture, with explicit sender-authorization and DKIM-order validation.
- Keep all integration changes isolated enough to rebase/merge current Mailcow upstream changes.

## Current source facts

The OpenSigWeave `dev` branch currently describes a standalone FastAPI/Uvicorn application with a Jinja portal, PostgreSQL via SQLAlchemy/Alembic, a separate signature filesystem volume, a separate identity-provider secret volume, and its own encrypted backup/restore CLI and systemd scheduler. Its architecture document explicitly treats native Mailcow integration as a separate future project.

The current Mailcow Compose stack already provides MariaDB, Redis, PHP-FPM, Nginx and Rspamd. The Nginx service mounts `./data/web` read-only at `/web`; PHP-FPM serves the Mailcow PHP application. Mailcow's web header prepares built-in application links and configurable app links. The integration should use existing Mailcow session and navigation mechanisms rather than expose a second web application.

## Proposed target architecture

1. **Native PHP presentation layer:** Add signature-management pages/routes and navigation within Mailcow's existing web application. Enforce permissions server-side on every operation.
2. **Mailcow identity adapter:** Derive the current user, role, mailbox and domain scope from the authenticated Mailcow session and existing database records. No independent OIDC/LDAP/local-login configuration is needed for this distribution.
3. **Persistence adapter:** Move OpenSigWeave metadata to a dedicated set of MariaDB tables in Mailcow's existing database service. Use prefixed tables and versioned, idempotent migrations. Do not modify Mailcow's existing tables unless strictly necessary.
4. **Signature file store:** Place current HTML and immutable versions under a dedicated Mailcow data directory. Confirm the normal backup script includes it; if not, extend the supported backup/restore path rather than adding a second backup system.
5. **Engine integration:** Prefer reusing the existing OpenSigWeave engine behavior behind a private, non-public service boundary only if that is materially safer and smaller than porting it. Any internal service must have no independent public UI, no separate database and no standalone backup lifecycle. Evaluate a PHP-native engine versus a private Python API before committing to one.
6. **Rspamd integration:** Reuse/adapt the Lua adapter only after validating Mailcow's actual Rspamd load order, authenticated SMTP identity, envelope-sender authorization, MIME behavior and final DKIM result.

## Migration and compatibility requirements

- MariaDB compatibility must be demonstrated for all model types, indexes, constraints, JSON fields, transactions and Alembic migrations before choosing to retain SQLAlchemy.
- Existing OpenSigWeave behavior and security controls must not silently disappear during UI/authentication removal.
- Preserve signature IDs, immutable version history and directory naming semantics where possible.
- Keep standalone-only features out of the Mailcow deployment: standalone login page, managed identity-provider configuration, independent PostgreSQL service, independent Compose stack, backup CLI, systemd backup timer and standalone reverse-proxy instructions.
- Never expose the internal engine endpoint publicly. Authenticate and authorize all calls from Mailcow components.
- Avoid modifying generated Mailcow configuration or vendor container images unless the upstream project requires it.

## Implementation sequence

1. Map the exact OpenSigWeave `dev` source tree and identify reusable domain, rule, rendering, sanitization and engine modules.
2. Map Mailcow's PHP routing, session/role checks, navigation, database connection helpers, schema migration conventions, Rspamd configuration and backup script.
3. Write an implementation design with explicit file-level changes and a permission matrix.
4. Implement a minimal vertical slice: navigation entry, Mailcow-session authorization, create/list/edit one signature, MariaDB persistence and backup inclusion.
5. Port remaining features incrementally: versions, variants, rules, preview/sanitization, user/domain scopes and Rspamd engine integration.
6. Add CI for PHP linting, Python tests if retained, migration tests against MariaDB, authorization tests, Compose validation and Rspamd integration tests.
7. Rebase regularly on Mailcow upstream and prepare a draft PR only after the feature is independently testable.

## Non-goals

- A separate OpenSigWeave login page or externally reachable portal.
- A second PostgreSQL deployment.
- A parallel backup scheduler, retention manager or restore CLI.
- Unreviewed edits to Mailcow's existing mailbox/domain permissions.
- Enabling signature injection in production before sender authorization and DKIM ordering are proven.


## Verified Mailcow integration points

The current Mailcow source was inspected at the following paths:

- `data/web/inc/prerequisites.inc.php`: initializes the existing PDO connection to MariaDB, Redis, authentication helpers and session handling.
- `data/web/inc/vars.inc.php`: configures `$database_type = 'mysql'`, database credentials from `DBUSER` / `DBPASS`, and the existing Mailcow app-link configuration.
- `data/web/inc/header.inc.php`: prepares built-in and configurable app links for the rendered UI.
- `data/web/admin/dashboard.php`: demonstrates the native PHP page pattern and `protect_route(['admin'])`.
- `data/web/inc/triggers.admin.inc.php` and `triggers.user.inc.php`: show that Mailcow login establishes `mailcow_cc_username` and `mailcow_cc_role` in the session.
- `helper-scripts/backup_and_restore.sh`: the standard backup currently enumerates specific named volumes (vmail, crypt, Redis, Rspamd, Postfix and MariaDB). A new signature volume would not be included automatically; its backup and restore paths must be added explicitly, or signature content must be stored in MariaDB.

The current Mailcow PHP application already has the authenticated PDO connection and session context available to native pages. We should not create another login flow.

## Refined implementation direction

The preferred low-duplication option is a **headless internal OpenSigWeave engine**, with a native Mailcow PHP frontend:

- Mailcow PHP owns all browser routes, layout, session checks, CSRF handling and role/domain authorization.
- The Python code is reduced to an internal engine/API only. It has no public port, no standalone portal, no login page and no managed identity-provider UI.
- The internal service uses Mailcow's existing MariaDB service and a dedicated schema/table prefix. No PostgreSQL service is deployed.
- Mailcow's PHP frontend calls only the private engine API. Requests must be authenticated server-to-server; browser-supplied identity headers must never be trusted.
- Mailcow mailbox/domain records provide identity attributes and scope. Authentik/LDAP provider management from standalone OpenSigWeave is removed from this distribution; Mailcow's configured identity flow remains authoritative.
- Rspamd calls the same private engine endpoint for signature selection and rendering.

This option preserves the existing Python rule/selection/rendering/sanitization logic better than an immediate full rewrite in PHP, while the user-facing application remains native Mailcow. Before implementation, verify the MariaDB driver and all Alembic migrations against the exact Mailcow MariaDB version. If the migrations or dialect behavior are not safe, implement a dedicated MariaDB persistence adapter rather than forcing incompatible migrations.

### Backup decision

The preferred initial storage option is a dedicated named volume for signature HTML and immutable version snapshots, with explicit additions to both backup and restore handling in `helper-scripts/backup_and_restore.sh`. This keeps the existing filesystem versioning model and makes Mailcow's existing backup command the only backup workflow. The volume must be included in backup selection, restore selection, validation and documentation; adding only the Compose volume is insufficient.

## Current implementation state

This document records the intended architecture only. No OpenSigWeave application code has been imported or integrated yet.