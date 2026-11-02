#!/usr/bin/env bash
# osgallery-dev: wait for the three small-plan proofs, then pull sources and products.
set -euo pipefail
SOURCE_VERSION=${1:?frozen source version required}
[[ $SOURCE_VERSION =~ ^[a-f0-9]{64}$ ]]
BULK_HOST=${BULK_HOST:-root@185.20.139.132}
BULK_ROOT=${BULK_ROOT:-/root/turku3d-bulk-gen2}
[[ $BULK_ROOT =~ ^/[a-zA-Z0-9/_-]+$ ]]
RECEIVER_FARM=${RECEIVER_FARM:-/home/wnt/turku3d-local/loops/bulkgen2/farm}
SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
mkdir -p "$RECEIVER_FARM/receipts"
exec 9>"$RECEIVER_FARM/receipts/pull-proofs.lock"
flock -n 9
ssh "$BULK_HOST" "flock -x '$BULK_ROOT/state/proof-driver.lock' true"
ssh "$BULK_HOST" "python3 - '$SOURCE_VERSION' '$BULK_ROOT'" <<'PY'
import json,sys
from pathlib import Path
state=Path(sys.argv[2])/'state'
result=json.loads((state/'bulk-result.json').read_text())
build=json.loads((state/'build.json').read_text())
assert build['selected']==['t0_0_0','t0_1_0','t0_-1_0'] and build['workers']==1
assert result['source_version']==sys.argv[1]
assert not any(result[k] for k in ('failures','remaining','disk_pressure','interrupted'))
assert result['metrics']['builds']+result['already_complete']==3
print('Three validated small-plan proof products complete; source pull admitted')
PY
ssh "$BULK_HOST" "cat '$BULK_ROOT/state/build.json'" > "$RECEIVER_FARM/receipts/proof-build.json"
python3 "$SCRIPT_DIR/transfer.py" --host "$BULK_HOST" --remote-root "$BULK_ROOT" \
  --root "$RECEIVER_FARM" --sources "$SOURCE_VERSION" \
  --link-dest /home/wnt/turku3d-local/combined/data
python3 "$SCRIPT_DIR/transfer.py" --host "$BULK_HOST" --remote-root "$BULK_ROOT" \
  --root "$RECEIVER_FARM" --build-file "$RECEIVER_FARM/receipts/proof-build.json" --tiles t0_0_0 t0_1_0 t0_-1_0
