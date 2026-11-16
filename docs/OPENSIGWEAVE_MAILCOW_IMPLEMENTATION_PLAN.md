# OpenSigWeave Mailcow Implementation Plan

**Status:** Design validated against current source; implementation not started  
**Active branch:** `dev`  
**Target:** Mailcow's existing Compose, PHP-FPM, MariaDB, Rspamd and backup workflow

## 1. Architecture decision

Use the existing Mailcow footer injection pipeline as the only MIME injection path.

`MOO_FOOTER` already runs in Rspamd, obtains the authenticated SMTP username and envelope sender, requests footer JSON from `http://nginx:8081/footer.php`, and inserts HTML/plain content with Rspamd's MIME helpers. OpenSigWeave should become an optional provider behind that existing endpoint.

Do not install the standalone `rspamd/opensigweave.lua` module as a second independent injector unless a later test proves the native footer pipeline cannot support a required feature.

## 2. Message-flow contract

Target flow:

1. Postfix authenticates the SMTP submission and passes the authenticated username to Rspamd.
2. Rspamd's existing `MOO_FOOTER` callback calls Mailcow's existing `footer.php` endpoint.
3. `footer.php` resolves the envelope sender domain and alias-domain target using Mailcow's own database.
4. If OpenSigWeave is enabled for that target domain, `footer.php` calls the private OpenSigWeave engine using the authenticated mailbox identity. The server-to-server request carries a shared key and an allowlisted profile payload sourced from Mailcow's `mailbox.custom_attributes`; it must not trust profile data supplied by the SMTP client.
5. The Mailcow engine mode constructs its identity context from that payload and Mailcow's stable mailbox identity. It must not call OpenSigWeave's standalone `get_identity_attributes()` provider path or open its own LDAP/Active Directory/AuthentiK connection.
6. The adapter converts the engine response into Mailcow's existing footer JSON contract: `html`, `plain`, `skip_replies`, and `vars`.
6. If OpenSigWeave is not enabled, the existing `domain_wide_footer` lookup and response remain unchanged.
7. The existing `MOO_FOOTER` callback performs the MIME rewrite. No second plugin injects another signature.

The adapter must preserve the distinction between authenticated username and envelope sender. The envelope sender must never be treated as proof of mailbox identity. Mailcow's sender authorization remains authoritative.

## 3. File-level implementation map

### Engine image and runtime

- Add an OpenSigWeave engine build context under `data/Dockerfiles/opensigweave/`. Its source, build and lifecycle are part of this `mailcow-dockerized` repository and its existing Compose deployment; it must not require a separately installed OpenSigWeave checkout or external service.
- Import only the Python modules needed by the API engine, persistence, signature storage, rules, selection, rendering and sanitization.
- Add an explicit headless runtime mode. In headless mode, only health/readiness and the authenticated signature-resolution API are reachable; login, portal, static, admin, identity-provider and backup endpoints return 404.
- Provide a Mailcow-specific signature-resolution request that accepts a bounded JSON profile payload from `footer.php`; do not use the standalone identity-provider registry/cache or `get_identity_attributes()` in Mailcow mode. Use the authenticated mailbox's stable Mailcow identity as the principal for deterministic/group selection.
- Keep the engine on the existing `mailcow-network`; publish no host port.
- Use the existing Mailcow MariaDB service. Do not add PostgreSQL or a second database container.
- Use a generated internal API key and a separate internal session secret if the existing FastAPI startup still requires one. Neither value may be committed to Git.

### MariaDB persistence

- Add a dedicated `osw_` namespace to every OpenSigWeave table and the Alembic version table.
- The source-level audit found that upstream currently has no MariaDB driver, has unbounded indexed `String()` columns in the initial schema, and uses `IS DISTINCT FROM` in migration 0012's downgrade guard. These are concrete blockers, not assumptions; see [the MariaDB compatibility audit](OPENSIGWEAVE_MARIADB_COMPATIBILITY_AUDIT.md).
- Add the MySQL/MariaDB SQLAlchemy driver.
- Correct unbounded indexed string columns and any PostgreSQL/SQLite-specific migration expressions.
- Validate the complete migration chain on MariaDB 10.11 from an empty schema.
- Test migration from the prior OpenSigWeave schema only if a supported data-import path is explicitly required; do not run standalone migrations against Mailcow's existing tables.

### Native Mailcow UI

- Add native PHP routes under the existing web tree, using `prerequisites.inc.php`, the matching role trigger and `protect_route()`.
- Use the existing PDO connection, Twig templates, CSRF token and Mailcow session.
- Implement authorization in every read/write action:
  - `admin`: global OpenSigWeave configuration and all domains.
  - `domainadmin`: only domains for which Mailcow's `hasDomainAccess()` returns true.
  - `user`: only the authenticated mailbox's personal override, if enabled by domain policy.
- Use Mailcow domain/mailbox records as the authority for existence and ownership. Never accept a domain or mailbox scope solely because the browser supplied it.
- Keep standalone OpenSigWeave login and identity-provider management out of the Mailcow distribution.

### Footer adapter

- Extend `data/conf/rspamd/dynmaps/footer.php` with an OpenSigWeave provider branch.
- Keep the existing footer lookup as the fallback path.
- Preserve current Mailcow alias-domain resolution, mailbox exclusions, alias-domain exclusions and reply-skip policy.
- Return the same JSON structure expected by `MOO_FOOTER`; do not change the MIME-rewrite implementation unless tests show it is necessary.
- Add bounded connection/read timeouts, response-size limits, JSON validation and fail-closed/fallback behavior. Do not log signature HTML, API keys or mailbox custom attributes.
- Add explicit tests for send-as aliases and the authenticated-user/envelope-sender distinction.

### Signature storage and backup

- Keep the existing name-based layout and immutable version snapshots:
  `signatur/{name}/signature.html` plus `.versions/{version-id}.html`.
- Store files in a dedicated Mailcow-managed path/volume, not inside the container writable layer.
- Extend `helper-scripts/backup_and_restore.sh` so the signature archive is included in explicit and `all` backup/restore operations.
- Extend `helper-scripts/dev_tests/test_backup_and_restore.sh` for archive creation, restore, missing archive and path validation.
- Ensure backup/restore preserves ownership and permissions.

## 4. User profile attributes (existing directory connection only)

OpenSigWeave does not create its own LDAP/Active Directory connection.

The Mailcow LDAP identity sync now requests the allowlisted profile attributes in the same paged LDAP search and through the same configured connection it already uses for identity synchronization. The current mapping is:

- `givenName` → `first_name`
- `sn` → `last_name`
- `title` → `title`
- `telephoneNumber` → `phone`, falling back to `mobile` when no telephoneNumber is present
- `displayName`, `givenName`, `sn`, `title`, `telephoneNumber`, `mobile`, `department` → the generic `attributes` map

The existing SyncPlanner and SyncService pass this profile data to the Mailcow identity repository. It stores the normalized object under `mailbox.custom_attributes.opensigweave`. The update uses MariaDB JSON functions to replace only that reserved namespace while preserving unrelated `custom_attributes` keys. The namespace is owned by identity sync: values removed or cleared in AD disappear on the next successful sync; manual edits inside this namespace are overwritten. Manual values in other namespaces are preserved.

The existing `footer.php` reads `mailbox.custom_attributes` at send time and passes the OpenSigWeave namespace to the private engine. No directory request occurs while sending mail. The SOGo `c_telephonenumber` field is not used as a substitute. In Mailcow mode, the standalone OpenSigWeave identity-provider registry, cache and provider lookup are bypassed entirely.

This mapping currently applies to Mailcow's LDAP identity provider. Keycloak synchronization remains unchanged. Group membership/claims are not yet populated into the OpenSigWeave profile.

## 5. Critical behavior to preserve

- Stable signature IDs when a signature is renamed.
- Immutable, checksummed signature versions.
- Signature modes, variants, groups and weighted selection.
- Rule priority and rule-condition semantics.
- HTML sanitization and output-size limits.
- Existing Mailcow footer fallback when OpenSigWeave is disabled.
- Existing Mailcow mailbox and alias-domain exclusions.
- Reply handling and plain/HTML MIME behavior.
- Mailcow's SMTP authentication and sender authorization.
- DKIM signing after final message-body modification.

## 6. Implementation order

1. Add MariaDB compatibility tests and make the persistence namespace work.
2. Extend Mailcow's existing identity sync to import explicitly mapped profile attributes through its existing directory connection; test preservation of unrelated `custom_attributes`.
3. Extract and test the headless engine runtime without copying the standalone UI.
4. Add the Compose service with no published ports and readiness checks.
5. Implement the PHP-to-engine adapter behind `footer.php`, with the existing Mailcow footer as fallback.
6. Add the native PHP/Twig management pages and role-scoped authorization.
7. Add signature-volume backup/restore support and regression tests.
8. Add CI for PHP lint, Python unit tests, MariaDB migrations, Compose validation, identity-sync mapping, footer contract and Rspamd configuration.
9. Test actual SMTP submission with normal mailbox, send-as alias, alias domain, reply, HTML-only, plain-text and multipart messages. Verify the final DKIM signature.

## 7. Explicit non-goals

- A second public web application or login page.
- A second PostgreSQL/MariaDB container.
- A second Rspamd MIME injector.
- Replacing Mailcow's SMTP authentication or sender ACL.
- Disabling TLS verification or exposing the engine port publicly.
- Enabling injection before real SMTP and DKIM tests pass.

## 8. Current implementation state

### Implemented on dev

- Added a Mailcow-specific SQLAlchemy database factory using Mailcow's existing DBNAME, DBUSER and DBPASS values and MariaDB.
- Added an isolated persistence model set under the osw_ table namespace and an osw_alembic_version migration table.
- Added an initial Alembic migration and static MySQL-dialect schema checks.
- Vendored provider-neutral OpenSigWeave rule, selection and variant modules from upstream OpenSigWeave dev commit 93b360225c5008fb60f6598e431ee2e3ff03842d.
- Added MariaDB-specific upsert support to per-user variant rotation and group rotation.
- Vendored sandboxed Jinja rendering, HTML allowlist sanitization and hardened immutable signature storage.
- Added a private headless FastAPI engine. Its request contract distinguishes authenticated mailbox, SMTP envelope sender and Mailcow-resolved target domain. It accepts profile attributes from the trusted Mailcow caller and does not query LDAP/OIDC.
- Added a non-root Docker image and an entrypoint that fixes named-volume ownership before dropping privileges.
- Added a private Compose service with no published port and a dedicated engine key. New installs generate the key in generate_config.sh; update.sh adds or repairs it on existing installations. Injection remains disabled by default.
- Extended Mailcow's existing PHP footer endpoint to call the engine only when explicitly enabled. The existing Mailcow footer remains the fallback, and Rspamd remains the only MIME injector.
- Added backup/restore handling for the persistent signature volume.
- Extended the existing LDAP identity sync to request an explicit allowlist (displayName, givenName, sn, title, telephoneNumber, mobile, department) in its existing paged LDAP search and persist normalized profile values into mailbox.custom_attributes.opensigweave. No additional LDAP connection or bind is created. Unrelated custom_attributes keys are preserved; the reserved OpenSigWeave namespace is sync-owned and missing source values are cleared on the next successful sync.
- Added a pinned upstream synchronization script for the provider-neutral modules; the MariaDB-specific selection repositories remain deliberate three-way merges.
- Added a MariaDB JSON namespace test proving that LDAP profile updates preserve unrelated mailbox custom attributes and replace only the sync-owned OpenSigWeave namespace.
- Added GitHub Actions checks for pinned upstream synchronization, MariaDB schema and selection, MariaDB JSON profile persistence, engine HTTP boundary, Docker image build, PHP syntax, 447 Mailcow PHP unit tests, shell syntax and Compose configuration.

### Not implemented yet

- Native Mailcow management pages and role authorization.
- Rspamd integration tests for reply handling, plain/HTML and multipart MIME.
- Verification/implementation of trim-whitespace and device-signature policies in the Mailcow Rspamd path.
- End-to-end SMTP, alias/send-as and DKIM tests on a disposable Mailcow deployment.

The initial migration currently creates the declared metadata tables with SQLAlchemy's create_all. This is suitable only as the first isolated schema bootstrap; all future schema changes must be explicit forward Alembic revisions. Do not run this migration against a live Mailcow database until the complete integration and backup procedure have been reviewed.

The full integration CI run [#43848984343](https://github.com/VeiglbergTech/mailcow-OpenSigWeave/actions/runs/43848984343) passed 447 PHP tests and 15 Python tests. It also passed the pinned upstream sync reproducibility check, Docker image build, PHP lint, shell lint, complete Compose configuration validation and MariaDB migration/selection/profile tests.

Signature injection is disabled by default. The integration is not production-ready until the native management UI and real SMTP/DKIM tests are complete.
