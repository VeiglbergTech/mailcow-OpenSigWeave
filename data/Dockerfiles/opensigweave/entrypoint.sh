#!/bin/sh
set -eu

storage_path="${SIGNATURE_STORAGE_PATH:-/var/lib/opensigweave}"
mkdir -p "$storage_path"
chown -R opensigweave:opensigweave "$storage_path"

exec gosu opensigweave:opensigweave "$@"
