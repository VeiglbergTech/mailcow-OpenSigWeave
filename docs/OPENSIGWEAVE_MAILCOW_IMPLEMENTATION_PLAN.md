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
4. If OpenSigWeave is enabled for that target domain, `footer.php` calls the private OpenSigWeave engine using the authenticated mailbox identity. The engine request is server-to-server only and carries a shared key.
5. The adapter converts the engine response into Mailcow's existing footer JSON contract: `html`, `plain`, `skip_replies`, and `vars`.
6. If OpenSigWeave is not enabled, the existing `domain_wide_footer` lookup and response remain unchanged.
7. The existing `MOO_FOOTER` callback performs the MIME rewrite. No second plugin injects another signature.

The adapter must preserve the distinction between authenticated username and envelope sender. The envelope sender must never be treated as proof of mailbox identity. Mailcow's sender authorization remains authoritative.

## 3. File-level implementation map

### Engine image and runtime

- Add an OpenSigWeave engine build context under `data/Dockerfiles/opensigweave/`. Its source, build and lifecycle are part of this `mailcow-dockerized` repository and its existing Compose deployment; it must not require a separately installed OpenSigWeave checkout or external service.
- Import only the Python modules needed by the API engine, persistence, signature storage, rules, selection, rendering and sanitization.
- Add an explicit headless runtime mode. In headless mode, only health/readiness and the authenticated signature-resolution API are reachable; login, portal, static, admin, identity-provider and backup endpoints return 404.
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

OpenSigWeave must not create its own LDAP/Active Directory connection.

Use Mailcow's existing identity provider configuration and scheduled identity-sync connection. The current LDAP sync reads identity fields and one optional template-mapping attribute, but does not generally import `telephoneNumber`, `department`, `title` or similar profile fields into `mailbox.custom_attributes`.

Extend that existing sync to request an explicit allowlist of profile attributes in the same LDAP search and persist them under a reserved OpenSigWeave namespace in `mailbox.custom_attributes`. Preserve unrelated custom attributes and define how empty/deleted AD values and manual overrides are handled.

The existing `footer.php` already returns `mailbox.custom_attributes` as footer `vars`, so OpenSigWeave can consume the synchronized profile data without contacting AD/LDAP at message-send time. The SOGo `c_telephonenumber` schema field must not be assumed to populate these footer variables.

Initial candidate AD mappings: `displayName`, `telephoneNumber`, `mobile`, `department`, `title`, `givenName`, `sn`. The final mapping and placeholder names are to be confirmed.

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

Only documentation has been committed so far. The MariaDB source-level audit is complete, but no engine files, Compose service, PHP adapter, native UI, database migrations or backup changes have been committed. No MariaDB runtime migration has been executed. The next code milestone is the Mailcow-specific persistence namespace and its MariaDB 10.11 test harness, followed by the headless engine proof of concept.
