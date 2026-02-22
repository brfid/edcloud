#!/usr/bin/env bash
# Append a restore-drill record to ~/.config/edcloud/restore-drill.tsv.
set -euo pipefail

result="${1:-pass}"
snapshot_id="${2:-unknown}"
notes="${3:-}"

log_file="${EDCLOUD_RESTORE_LOG:-$HOME/.config/edcloud/restore-drill.tsv}"
mkdir -p "$(dirname "$log_file")"

if [[ ! -f "$log_file" ]]; then
  printf "timestamp_utc\tresult\tsnapshot_id\tnotes\n" > "$log_file"
fi

timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
safe_notes="${notes//$'\t'/ }"
printf "%s\t%s\t%s\t%s\n" "$timestamp" "$result" "$snapshot_id" "$safe_notes" >> "$log_file"

echo "Recorded restore drill in $log_file"
