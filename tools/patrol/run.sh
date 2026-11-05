#!/bin/sh
# One patrol iteration end to end (tools/patrol/README.md): colour pass, id pass, drives, panorama crops, detectors.
#   tools/patrol/run.sh HOST SPOTS.json [OUT]      HOST = air | win (mac only when both are taken)
# Each browser stage takes its own lease through session.py (queued behind other agents, released when the stage
# ends). The judge and rank.py run afterwards (judge-brief.md).
set -u
HOST=$1; SPOTS=$2; OUT=${3:-$HOME/turku3d/loops/patrol/$(date +%Y%m%d-%H%M)}
S=$HOME/turku3d/tools/host-lease/session.py; PY=$HOME/turku3d/envs/pipeline/bin/python
cd "$(dirname "$0")/../.." || exit 2
mkdir -p "$OUT"; echo "$OUT"
spots=$(python3 -c "import json,sys;print(','.join(s['id'] for s in json.load(open(sys.argv[1]))['spots']))" "$SPOTS")
drives=$(python3 -c "import json,sys;print(','.join(d['id'] for d in json.load(open(sys.argv[1])).get('drives',[])))" "$SPOTS")
stage() {  # stage NAME ARGS...: one leased browser run
  name=$1; shift
  python3 "$S" run "$HOST" patrol --kind func --load-timeout 180 --headless --window-size 960,540 --queue-timeout 3600 -- \
    node tools/patrol/capture.mjs --spots "$SPOTS" --out "$OUT" "$@" > "$OUT/session-$name.log" 2>&1
  echo "stage $name exit $? $(tail -1 "$OUT/session-$name.log")"
}
stage color --pass color --only "$spots"
cp "$OUT/run-color.json" "$OUT/run-color-spots.json" 2>/dev/null
stage id --pass id --only "$spots"
[ -n "$drives" ] && stage drives --pass color --only "$drives"
[ -f "$OUT/run-color-spots.json" ] && cp "$OUT/run-color-spots.json" "$OUT/run-color.json"
"$PY" tools/patrol/panos.py refs "$OUT" > "$OUT/panos.log" 2>&1; echo "panos exit $?"
"$PY" tools/patrol/analyze.py "$OUT" 2>&1 | grep -v Warning | tail -6
echo RUN-DONE "$OUT"
