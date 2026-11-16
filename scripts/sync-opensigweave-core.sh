#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPSTREAM_URL="${OPEN_SIG_WEAVE_UPSTREAM_URL:-https://github.com/VeiglbergTech/OpenSigWeave.git}"
UPSTREAM_REF="${OPEN_SIG_WEAVE_UPSTREAM_REF:-93b360225c5008fb60f6598e431ee2e3ff03842d}"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TEMP_DIR}"' EXIT

git clone --quiet --filter=blob:none --no-checkout "${UPSTREAM_URL}" "${TEMP_DIR}/upstream"
git -C "${TEMP_DIR}/upstream" checkout --quiet --detach "${UPSTREAM_REF}"
UPSTREAM_COMMIT="$(git -C "${TEMP_DIR}/upstream" rev-parse HEAD)"

# Only provider-neutral runtime modules are copied automatically.
# Selection repositories are Mailcow-specific because they contain MariaDB upserts.
FILES=(
  "core/modes.py"
  "core/rules.py"
  "core/variant_selector.py"
  "core/rule_repository.py"
  "signature_renderer.py"
  "signature_sanitizer.py"
  "signature_storage.py"
)

for relative_path in "${FILES[@]}"; do
  source_file="${TEMP_DIR}/upstream/${relative_path}"
  target_file="${REPOSITORY_ROOT}/data/Dockerfiles/opensigweave/${relative_path}"
  if [[ ! -f "${source_file}" ]]; then
    echo "Missing upstream module: ${relative_path}" >&2
    exit 1
  fi
  mkdir -p "$(dirname "${target_file}")"
  cp "${source_file}" "${target_file}"
done

printf '%s\n' "${UPSTREAM_COMMIT}" > "${REPOSITORY_ROOT}/data/Dockerfiles/opensigweave/UPSTREAM_COMMIT"
echo "Synced provider-neutral OpenSigWeave modules from ${UPSTREAM_COMMIT}."
echo "Review git diff, MariaDB-specific selection repositories, dependencies and tests before committing."
