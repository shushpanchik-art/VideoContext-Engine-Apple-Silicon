#!/bin/bash
# Compatibility entrypoint: use the canonical JSON consistency patch installer.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${VCE_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
exec /bin/bash "$ROOT/tools/bench/vce_patch_test_run_nsrspec_json.command" "$@"
