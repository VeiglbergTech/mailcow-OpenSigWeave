# Synchronizing the upstream OpenSigWeave core

The Mailcow integration vendors a deliberately small, provider-neutral part of
\`VeiglbergTech/OpenSigWeave\`. It does not depend on that repository at runtime.

## Current source baseline

The currently imported baseline is recorded in
\`data/Dockerfiles/opensigweave/UPSTREAM_COMMIT\`.

The sync script defaults to this exact upstream commit. To test a different
commit, set \`OPEN_SIG_WEAVE_UPSTREAM_REF\` when invoking it:

\`\`\`bash
OPEN_SIG_WEAVE_UPSTREAM_REF=<commit-or-ref> bash scripts/sync-opensigweave-core.sh
\`\`\`

## Automatically synchronized files

- \`core/modes.py\`
- \`core/rules.py\`
- \`core/variant_selector.py\`
- \`core/rule_repository.py\`
- \`signature_renderer.py\`
- \`signature_sanitizer.py\`
- \`signature_storage.py\`

The script does not copy the standalone application, identity providers,
database layer, models, Dockerfile, or Compose configuration.

## Files requiring a deliberate three-way merge

- \`core/selection_repository.py\`
- \`core/group_selection_repository.py\`

These two Mailcow files contain the MariaDB upsert support required for the
Mailcow database. Upstream currently has PostgreSQL/SQLite conflict handling.
When upstream changes either file, review the upstream diff and port relevant
changes without removing the MySQL/MariaDB implementation.

## Update procedure

1. Run the sync script with a reviewed upstream commit.
2. Inspect \`git diff\`, especially renderer, sanitizer and storage changes.
3. Review dependency changes against
   \`data/Dockerfiles/opensigweave/requirements.txt\`.
4. Three-way review the two MariaDB-specific selection repositories.
5. Run the complete GitHub Actions workflow.
6. Commit the vendored source and updated \`UPSTREAM_COMMIT\` together.

The script only copies source files; it never commits changes. No Git checkout
or upstream network access is required by the deployed engine.
