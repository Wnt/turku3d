#!/bin/bash
# Mac: power on the 64-core window with a frozen plan, in one command (only with the coordinator's go).
#   window-go.sh REVISION [--turku N] [--farm N] [--cities N] [--dry-run]
# 1. preflight on the host: the revision and its code are local, the Turku queue and city plan are installed, every
#    city checkout in the plan exists; prints the frozen window.json;
# 2. removes city tiles the rehearsal built with a checkout other than the plan's (they would be skipped as built);
# 3. stops the rehearsal (window-run.sh stop) and osgallery-dev's helper guard (the window has its own TTLs);
# 4. osgallery-dev `window.py up`: stop, plan 64 cores, start, 55-min backstop armed, in-server TTL verified;
# 5. on the host: window.json (frozen: revision, lanes, queue, plan) and window-run.sh start;
# 6. osgallery-dev pull-loop.sh (results home every 4 min, ready/manifest last).
# Defaults sized from the 16-core rehearsal (generator build ~0.8 core, <=4 GB; lanes <=3 GB): 30 rebuild workers,
# 24 Turku lanes (64 slots), 14 city lanes on 64 cores / 256 GB.
set -euo pipefail
G=${BULK_STATE:-$HOME/turku3d/bulk-cache}
OSG=osgallery-dev
OD=turku3d-local/loops/bulkgen3
REV=${1:?revision (64 hex) of the alias to run}; shift
TURKU=24; FARM=30; CITIES=14; DRY=
while [ $# -gt 0 ]; do
  case "$1" in
    --turku) TURKU=$2; shift 2 ;; --farm) FARM=$2; shift 2 ;; --cities) CITIES=$2; shift 2 ;;
    --dry-run) DRY=1; shift ;; *) sed -n 2,13p "$0"; exit 2 ;;
  esac
done
IP=$(python3 -c "import json; print(json.load(open('$G/server.json'))['ip'])")
H=root@$IP
HG=/home/wnt/turku3d-bulk-gen3
F=/home/wnt/turku3d-local/loops/tiles/farm
W="{\"revision\": \"$REV\", \"turku_lanes\": $TURKU, \"max_jobs\": 64, \"blocks\": 2, \"farm_workers\": $FARM,
 \"city_lanes\": $CITIES, \"plan\": \"$HG/cities-plan.json\", \"tiles_file\": \"$HG/pass-tiles.txt\"}"
echo "== preflight"
ssh "$H" "set -e; m=$F/revisions/$REV/revision.json; test -f \$m
  c=\$(python3 -c \"import json; print(json.load(open('\$m'))['code_revision'])\"); test -d $F/revisions/\$c/repo
  echo \"revision $REV code \$c\"; wc -l < $HG/queue-turku.txt | sed 's/^/turku priority list /'; wc -l < $HG/pass-tiles.txt | sed 's/^/turku tile set /'
  python3 -c \"
import json, os
for c in json.load(open('$HG/cities-plan.json'))['cities']:
    assert os.path.isdir(c['checkout'] + '/tools/cities'), c['checkout']
    print(c['name'], len(c['tiles']), 'tiles,', os.path.basename(c['checkout']))\""
echo "window.json: $W"
[ -n "$DRY" ] && { echo "dry run: nothing changed"; exit 0; }
echo "== stale city tiles (built with another checkout)"
ssh "$H" "python3 - <<'PY'
import json, os, shutil
from pathlib import Path
for c in json.load(open('$HG/cities-plan.json'))['cities']:
    out = Path(c['out']); log = out.parent / 'window-builds.jsonl'
    good = set()
    if log.exists():
        for r in map(json.loads, log.read_text().splitlines()):
            if r.get('state') == 'built' and r.get('checkout') == c['checkout']:
                good.add(r['tile'])
    gone = [t for t in c['tiles'] if (out / t / 'manifest.json').exists() and t not in good]
    for t in gone:
        shutil.rmtree(out / t)
    print(c['name'], 'kept', len(good), 'removed', len(gone))
PY"
echo "== stop the rehearsal and the helper guard"
ssh "$H" "$HG/window-run.sh stop; systemctl stop helper-ttl.timer"
ssh $OSG "cd $OD/window && python3 helper-guard.py remove"
echo "== window.py up (64 cores, backstop, in-server TTL)"
ssh $OSG "cd $OD/window && python3 window.py up" | tail -3
until ssh -o ConnectTimeout=5 -o BatchMode=yes "$H" "test \$(nproc) -ge 32 && test -s /run/bulk-ttl" 2>/dev/null; do sleep 5; done
echo "== start"
ssh "$H" "echo '$W' > $HG/window.json && chown 1000:1000 $HG/window.json && rm -f $HG/pass.pid $HG/cities.pid $HG/warm.pid &&
  $HG/window-run.sh start && cat /run/bulk-ttl"
ssh $OSG "cd $OD && setsid -f nohup bin/pull-loop.sh '$REV' > pull-loop.out 2>&1 < /dev/null"
date -u +"window started %FT%TZ"
