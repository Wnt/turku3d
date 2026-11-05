#!/bin/bash
# Bulk host: run a migrated alias's city pass on this host (all tiles, no radius), from the alias's code
# snapshot in the mirrored farm, exactly as `tile-migrate pass` does on osgallery-dev.
#   window-pass.sh ALIAS_OR_REVISION [extra tile_city_pass.py args...]     (background; prints pid and log paths)
#   Runs through pass-run.py: LANES_FILE (a lane count, re-read live) caps the lanes below MAX_JOBS (the lane slots,
#   default LANES); a lane whose worker dies gets a new one.
#   TILES_FILE=F takes the tile list from F (one per line; $G/pass-tiles.txt from an earlier EXCLUDE run).
#   EXCLUDE="t0_0_0 ..." leaves tiles out (e.g. the ones osgallery-dev builds itself); WARM=0 skips window-warm.py.
#   LANES (default: cores - 8), BLOCKS (default 2; memory, not cores, limits lanes: 2.4 GB at 1 block, 6-8 GB at 9), HIGH/LOW (load per core; default 1.3/1.1).
# The in-server TTL powers off at boot + 55 min; finished tiles (atomic ready.json) stay on disk, running ones
# are redone on the next run. Spot rebuilds (Rebuild steps) are requested from FARM_URL: by default the host's own farm
# service (host-farm.sh start N, 127.0.0.1:7950) on a cached host, else a dead port (stage them beforehand).
# Hard restart for mid-run tweaks: kill the process group (pid file), then rerun (finished tiles are kept).
set -euo pipefail
# Run as wnt (uid 1000), as on the farm: the runner finds its env under $HOME/turku3d-bulk-warmup.
[ "$(id -u)" = 1000 ] || exec su wnt -c "LANES=${LANES:-} BLOCKS=${BLOCKS:-} HIGH=${HIGH:-} LOW=${LOW:-} FARM_URL=${FARM_URL:-} LANES_FILE=${LANES_FILE:-} MAX_JOBS=${MAX_JOBS:-} EXCLUDE='${EXCLUDE:-}' TILES_FILE=${TILES_FILE:-} WARM=${WARM:-} $(printf '%q ' "$0" "$@")"
SEL=${1:?alias or derived revision}; shift
F=/home/wnt/turku3d-local/loops/tiles/farm
REV=$(python3 -c "import json; print(json.load(open('$F/aliases.json')).get('$SEL', '$SEL'))")
G=/home/wnt/turku3d-bulk-gen3
META=$F/revisions/$REV/revision.json
CODE=$(python3 -c "import json; print(json.load(open('$META'))['code_revision'])")
GEN=$(python3 -c "import json; print(json.load(open('$META'))['generation'])")
REPO=$F/revisions/$CODE/repo
[ -d "$REPO" ] || { echo "code snapshot $REPO missing: run sync-train.sh on osgallery-dev"; exit 1; }
CORES=$(grep -c ^processor /proc/cpuinfo)
LANES=${LANES:-}; LANES=${LANES:-$(( CORES > 16 ? CORES - 8 : 1 ))}
mkdir -p $F/work/migrate $F/logs $G/logs
# Decoded-LAZ cache trim (rebuildable; the gen2 run starved its disk on it).
setsid -f nohup python3 $G/trim-decoded.py $F/work/migrate/cache >> $G/logs/trim-decoded.log 2>&1 < /dev/null
cd $REPO
LOG=$F/logs/migrate-${CODE:0:12}-pass.log
RUN="env REPO=$REPO TURKU3D_WORK=$F/work/migrate TURKU3D_THREADS_PER_WORKER=1 /home/wnt/turku3d-local/loops/tiles/shared-tiles-run.sh"
TILES=$*
if [ -n "${TILES_FILE:-}" ]; then
  TILES="$TILES $(tr '\n' ' ' < "$TILES_FILE")"  # precomputed (listing the base over NFS takes about a minute)
elif [ -n "${EXCLUDE:-}" ]; then
  # The pass takes an explicit tile list: every finished base tile but the excluded ones, in its own ring order.
  $RUN python -c "import json, sys; sys.path.insert(0, 'scripts/dev'); import tile_migrate as M
meta = json.load(open('$META')); skip = set('$EXCLUDE'.split())
print('\n'.join(t for t in M.finished_base_tiles(M.Path('$F'), meta) if t not in skip))" > $G/pass-tiles.txt
  TILES="$TILES $(tr '\n' ' ' < $G/pass-tiles.txt)"
fi
if [ "${WARM:-1}" != 0 ]; then
  # Survey blocks of upcoming tiles supplied ahead of the lanes (one cold block is ~2 s on the tunnel).
  setsid -f nohup $RUN python $G/window-warm.py --farm-root $F --revision $REV \
    --supply /bulk/turku3d/lidarpc/raw/pistepilvi >> $G/logs/warm.log 2>&1 < /dev/null
fi
FARM_URL=${FARM_URL:-$(curl -sf -m2 -o /dev/null http://127.0.0.1:7950/health && echo http://127.0.0.1:7950 || echo http://127.0.0.1:9)}
setsid sh -c "LANES_FILE=${LANES_FILE:-} REPO=$REPO TURKU3D_WORK=$F/work/migrate TURKU3D_THREADS_PER_WORKER=1 \
  /home/wnt/turku3d-local/loops/tiles/shared-tiles-run.sh python $G/pass-run.py scripts/dev/tile_city_pass.py \
  --farm-root $F --revision $REV --max-jobs ${MAX_JOBS:-$LANES} --blocks ${BLOCKS:-2} --high ${HIGH:-1.3} --low ${LOW:-1.1} \
  --interval ${INTERVAL:-2} --min-free-gb 15 --min-free-mem-gb 8 --checkpoint wires@2 \
  --supply /bulk/turku3d/lidarpc/raw/pistepilvi --farm-url $FARM_URL $TILES >> $LOG 2>&1 < /dev/null" &
echo $! > $G/pass.pid
echo "{\"pid\": $(cat $G/pass.pid), \"lanes\": $LANES, \"log\": \"$LOG\", \"progress\": \"$F/migrations/$GEN/pass.json\"}"
