#!/usr/bin/env bash
# Run edc reprovision verification and save JSON output with a UTC timestamp.
set -euo pipefail

output_dir="${EDCLOUD_VERIFY_DIR:-$HOME/.config/edcloud/verify}"
mkdir -p "$output_dir"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
output_file="$output_dir/reprovision-verify-$timestamp.json"

edc verify --json-output | tee "$output_file"
echo "Saved verification report: $output_file"
