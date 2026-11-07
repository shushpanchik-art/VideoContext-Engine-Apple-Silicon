#!/bin/bash
# Backwards-compatible entry point: the canonical runner now performs the complete A/B.
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ "$#" -ne 0 ]]; then
  printf 'Этот launcher больше не принимает аргументы; настройки задаются через VCE_* environment variables.\n' >&2
  exit 2
fi
exec "$SCRIPT_DIR/vce_run_nsrspec_ab.command"
