# Synchronizing the upstream OpenSigWeave core

The Mailcow integration vendors a deliberately small, provider-neutral part of
`VeiglbergTech/OpenSigWeave`. It does not depend on that repository at runtime.

## Current source baseline

The Mailcow `beta` branch is reviewed against OpenSigWeave `beta`, pinned at
`eaa8dde852fdb6c52d21ef6babf430cc1a583cb1`. The exact imported revision is recorded in
`data/Dockerfiles/opensigweave/UPSTREAM_COMMIT`.

The sync script defaults to this reviewed commit. To test a different
upstream branch or commit, set `OPEN_SIG_WEAVE_UPSTREAM_REF` explicitly:

```bash
OPEN_SIG_WEAVE_UPSTREAM_REF=beta bash scripts/sync-opensigweave-core.sh
```

Do not leave a moving branch name as the default: CI and future rebuilds must
remain reproducible.

## Automatically synchronized files

- `core/modes.py`
- `core/rules.py`
- `core/variant_selector.py`
- `core/rule_repository.py`
- `core/slug.py`
- `signature_renderer.py`
- `signature_sanitizer.py`
- `signature_storage.py`

These eight provider-neutral runtime files have been compared against the
current upstream `beta` branch and are byte-for-byte identical at the reviewed
baseline.

## Changes intentionally not copied

The current upstream `beta` work also updates the standalone portal/editor,
including the CKEditor-based rich-text editor and its frontend assets. The
Mailcow engine is deliberately headless and does not ship the standalone
portal, login, identity-provider management or browser session stack.

Those UI changes must be implemented through native Mailcow PHP/Twig pages,
Mailcow session/CSRF handling and server-side role checks. Copying the
standalone `templates/`, `static/js/portal.js` or its API assumptions into
Mailcow would create a second portal and would not work with the current
headless engine. The native Mailcow management UI is implemented separately in
`data/web/admin/opensigweave.php`, `data/web/templates/opensigweave.twig` and
`data/web/js/site/opensigweave.js`. Its private management API is implemented
in `data/Dockerfiles/opensigweave/admin_api.py` and is reached through the
CSRF-protected PHP gateway. Continue testing the full UI and real mail flow;
never replace it with the standalone portal.

## Files requiring a deliberate three-way merge

- `core/selection_repository.py`
- `core/group_selection_repository.py`

These two Mailcow files contain the MariaDB upsert support required for the
Mailcow database. Upstream currently has PostgreSQL/SQLite conflict handling.
When upstream changes either file, review the upstream diff and port relevant
changes without removing the MySQL/MariaDB implementation.

## Update procedure

1. Review the upstream `beta` changes and pin the exact commit.
2. Run the sync script with that commit.
3. Inspect `git diff`, especially renderer, sanitizer and storage changes.
4. Review dependency changes against
   `data/Dockerfiles/opensigweave/requirements.txt`.
5. Three-way review the two MariaDB-specific selection repositories.
6. Run the complete GitHub Actions workflow.
7. Commit the vendored source and updated `UPSTREAM_COMMIT` together.

The script only copies source files; it never commits changes. No Git checkout
or upstream network access is required by the deployed engine.
