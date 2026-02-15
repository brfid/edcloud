#!/usr/bin/env bash
# Local operator wrapper for edc.
# Install to ~/.local/bin/edc and optionally set EDCLOUD_REPO.
set -euo pipefail

repo="${EDCLOUD_REPO:-$HOME/edcloud}"
bin="$repo/.venv/bin/edc"

if [[ ! -x "$bin" ]]; then
  echo "edc wrapper error: expected executable at $bin" >&2
  echo "Set EDCLOUD_REPO or install edcloud into $repo/.venv" >&2
  exit 1
fi

exec "$bin" "$@"
