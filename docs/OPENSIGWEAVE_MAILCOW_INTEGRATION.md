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
6. **Mail-flow integration:** Prefer extending Mailcow's existing `domain_wide_footer` / `MOO_FOOTER` / `footer.php` pipeline rather than installing a second independent Rspamd MIME injector. Preserve authenticated SMTP username versus envelope sender, existing exclusions, alias-domain handling, reply behavior and MIME rewriting. Only add a separate Lua plugin if the native pipeline cannot support the required behavior.

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

- Mailcow PHP owns all browser routes, layout, session checks, CSRF handling and role/domain authorization. The native signature-management UI is still to be implemented.
- The Python code is reduced to an internal engine/API only. It has no published host port, no standalone portal, no login page and no managed identity-provider UI.
- The internal service uses Mailcow's existing MariaDB service and a dedicated schema/table prefix. No PostgreSQL service is deployed.
- Mailcow's PHP footer adapter calls only the private engine API. Requests are authenticated server-to-server; browser-supplied identity headers are not trusted.
- Mailcow's existing LDAP identity sync fetches allowlisted profile attributes in the same paged LDAP search and writes them to the reserved mailbox custom-attributes namespace. No additional LDAP connection is created.
- Rspamd continues to call Mailcow's existing footer.php endpoint and remains the sole MIME injector. footer.php calls the private engine when explicitly enabled; the native Mailcow footer remains the fallback.

This option preserves the existing Python rule/selection/rendering/sanitization logic better than an immediate full rewrite in PHP, while the user-facing application remains native Mailcow. Before implementation, verify the MariaDB driver and all Alembic migrations against the exact Mailcow MariaDB version. If the migrations or dialect behavior are not safe, implement a dedicated MariaDB persistence adapter rather than forcing incompatible migrations.

### Backup decision

The preferred initial storage option is a dedicated named volume for signature HTML and immutable version snapshots, with explicit additions to both backup and restore handling in `helper-scripts/backup_and_restore.sh`. This keeps the existing filesystem versioning model and makes Mailcow's existing backup command the only backup workflow. The volume must be included in backup selection, restore selection, validation and documentation; adding only the Compose volume is insufficient.

## Source compatibility audit and implementation plan

A detailed source-level compatibility review is maintained in [`OPENSIGWEAVE_MAILCOW_COMPATIBILITY_AUDIT.md`](OPENSIGWEAVE_MAILCOW_COMPATIBILITY_AUDIT.md). The file-level target architecture and implementation order are in [`OPENSIGWEAVE_MAILCOW_IMPLEMENTATION_PLAN.md`](OPENSIGWEAVE_MAILCOW_IMPLEMENTATION_PLAN.md). It confirms that the existing signature catalog, filesystem version store, rule/selection modules, renderer, sanitizer and Rspamd adapter are reusable. It also records the MariaDB driver/schema, headless-startup, private transport, Rspamd loading and backup/restore blockers that must be resolved before runtime integration.

The Mailcow-mode engine contract is `POST /internal/signature` with the `X-Engine-Key` header. It explicitly separates `authenticated_user`, `envelope_sender` and Mailcow-resolved `target_domain`. Mailcow's `MOO_FOOTER` prefilter still retrieves footer JSON through its internal `footer.php` endpoint and performs MIME rewriting. The PHP adapter calls the private engine and falls back to the native Mailcow footer when the engine is disabled, unavailable or returns no signature. No second Rspamd MIME injector is installed.

## Current implementation state

The `beta` branch now contains a substantially working Mailcow-native management layer:

- OpenSigWeave runs as a private headless FastAPI engine against Mailcow's existing MariaDB service; no public engine port, second login system or PostgreSQL service is required.
- OpenSigWeave-owned tables stay in the reserved `osw_` namespace with an isolated Alembic version table. Mailcow-specific migrations are tested against MariaDB upgrades as well as fresh installations.
- The native Mailcow administration page manages domains, signatures, signature groups and rules through the existing Mailcow admin session and CSRF boundary.
- Signature editing stores immutable filesystem versions. The Mailcow UI exposes a sandboxed server-rendered preview, version history and restore; restore creates a new current version instead of rewriting history.
- The OpenSigWeave administration page follows the normal Mailcow/Bootstrap presentation and does not introduce separate application branding.
- MariaDB-compatible variant/group rotation, rule evaluation, HTML sanitization, template rendering and Mailcow LDAP profile attributes are integrated.
- Signature files remain in the Mailcow-managed persistent volume and are included in the existing Mailcow backup/restore path.
- Mailcow's existing `MOO_FOOTER` / `footer.php` / Rspamd path remains the only message-body modifier. The standalone Postfix Milter and Stalwart adapters are not installed in this distribution.

Current validation covers Python compilation, the engine image build, PHP unit/lint checks, Lua syntax, JavaScript syntax, Compose validation, MariaDB schema/migration tests and HTTP boundary tests.

### Deliberately not ported yet

- **Standalone branding:** Mailcow owns the application shell and visual identity. OpenSigWeave therefore intentionally has no separate branding settings, logo, favicon or accent-color subsystem in this distribution.

- **Scheduling:** standalone OpenSigWeave `dev` now has time-bounded signature/group scheduling. It is intentionally not enabled in the Mailcow distribution yet because the Mailcow behavior and UX are still being decided.
- **Reply/MIME rewriting:** OpenSigWeave signatures use the internal parser-based MIME rewriter from the existing Rspamd prefilter. The native Mailcow footer fallback deliberately remains on Mailcow's upstream `lua_mime.add_text_footer()` path.
- **Standalone identity-provider management, local/OIDC login and standalone backup tooling:** these remain non-goals because Mailcow supplies the corresponding platform services.
- **Standalone Postfix/Stalwart adapters:** they remain adapter-specific code and are not copied into Mailcow's Rspamd integration.
- **Rich editor:** Mailcow now uses a locally bundled GPL CKEditor 49.1.0 build with the same email-HTML preservation boundary as standalone OpenSigWeave; browser-side CDN loading is not required.

### DKIM ordering guard

OpenSigWeave mutates the message inside Mailcow's existing `MOO_FOOTER` Rspamd prefilter. Mailcow's DKIM signing remains a later normal Rspamd symbol and signs the final MIME body/header representation, including `Content-Type`, `Content-Transfer-Encoding` and `MIME-Version`. CI contains a repository-level ordering guard plus a runtime Rspamd API smoke test. This reduces regression risk but does not replace a live SMTP submission/receipt test against a deployed Mailcow instance.

### Production status

The management and persistence layers are CI-validated, but the integration is **not yet production-ready for automatic signature injection**. Real SMTP/DKIM end-to-end validation is still outstanding; parser-level MIME and reply-placement regression tests are now part of CI. Keep injection disabled on production systems until those checks are complete.
