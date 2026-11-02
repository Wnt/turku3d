#!/usr/bin/env bash
# Only the three authorized small-plan proof tiles; never a city build.
set -euo pipefail
BULK_ROOT=${BULK_ROOT:-/root/turku3d-bulk-gen2}
exec 8>"$BULK_ROOT/state/proof-driver.lock"
flock -n 8
flock -x "$BULK_ROOT/state/prefetch-resume.lock" true
# Both commands independently require complete acquisition and lock against writes.
"$BULK_ROOT/run.sh" python "$BULK_ROOT/ops/freeze.py" "$BULK_ROOT"
exec "$BULK_ROOT/run.sh" python "$BULK_ROOT/ops/bulk.py" "$BULK_ROOT" \
  --tiles t0_0_0 t0_1_0 t0_-1_0 --workers 1
