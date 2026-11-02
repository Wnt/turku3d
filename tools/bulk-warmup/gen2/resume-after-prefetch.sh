#!/usr/bin/env bash
# One owned continuation: wait for the initial source lock, then retry known gaps.
set -euo pipefail
BULK_ROOT=${BULK_ROOT:-/root/turku3d-bulk-gen2}
exec 9>"$BULK_ROOT/state/prefetch-resume.lock"
flock -n 9
flock -x "$BULK_ROOT/state/prefetch.lock" true
exec "$BULK_ROOT/run.sh" python "$BULK_ROOT/ops/prefetch.py" "$BULK_ROOT/state" \
  --workers 3 --adopt-initial-receipts
