#!/bin/bash
# Bulk host (as wnt): one first+full tile build for helper-build.sh. Holds a slot (helper-ttl.sh keeps the host on
# while any slot is held; at most SLOTS builds at once, default 4 on 16 cores).
#   helper-run.sh farm REV TILE      a registered farm revision (64 hex): the host farm service builds it exactly as
#                                    the farm would (products/<generation>/TILE, ready.json last); prints the result
#   helper-run.sh commit SHA TILE    a code tree already unpacked at $G/commits/SHA (helper-build.sh sends it): a
#                                    generator build with the farm's environment (source version DATA, default the
#                                    train base's; shared stage caches) into $G/out/SHA/TILE
set -euo pipefail
[ "$(id -u)" = 1000 ] || exec su wnt -c "DATA=${DATA:-} SLOTS=${SLOTS:-} $(printf '%q ' "$0" "$@")"
MODE=${1:?farm|commit}; ID=${2:?revision or commit}; TILE=${3:?tile}
G=/home/wnt/turku3d-bulk-gen3
T=/home/wnt/turku3d-local/loops/tiles
F=$T/farm
S=/run/helper/slots
mkdir -p "$S" "$G/logs"
# A slot: lock file N held for the build's lifetime, with this shell's pid for helper-ttl.sh.
exec 9>/dev/null
for _ in $(seq 3600); do
  for n in $(seq "${SLOTS:-4}"); do
    exec 9>"$S/lock.$n"
    if flock -n 9; then echo $$ > "$S/$n"; trap 'rm -f "$S/$n"' EXIT; break 2; fi
  done
  sleep 2
done
"$G/helper-ttl.sh" extend
t0=$(date +%s)
case $MODE in
  farm)
    GEN=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['generation'])" "$F/revisions/$ID/revision.json")
    curl -sf -m2 -o /dev/null http://127.0.0.1:7950/health || "$G/host-farm.sh" start 4 > /dev/null
    until [ -f "$F/products/$GEN/$TILE/ready.json" ]; do
      code=$(curl -s -m30 -o "$G/logs/helper-$TILE.req" -w '%{http_code}' "http://127.0.0.1:7950/v/$ID/coarse/$TILE/manifest.json")
      [ "$code" = 200 ] && [ -f "$F/products/$GEN/$TILE/ready.json" ] && break
      if [ "$code" -ge 400 ] && [ "$code" != 404 ] && [ "$code" != 409 ] && [ "$code" != 503 ]; then
        echo "{\"tile\": \"$TILE\", \"state\": \"failed\", \"http\": $code, \"body\": $(head -c 400 "$G/logs/helper-$TILE.req" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}"
        exit 1
      fi
      sleep 10
    done
    python3 - "$F/products/$GEN/$TILE/ready.json" "$GEN" "$TILE" "$(( $(date +%s) - t0 ))" <<'PY'
import json, sys
ready = json.load(open(sys.argv[1]))
print(json.dumps(dict(tile=sys.argv[3], state='built', generation=sys.argv[2], wall=int(sys.argv[4]),
                      build_seconds=ready.get('seconds'), stage_hits=ready.get('stage_hits'),
                      stage_misses=ready.get('stage_misses'), path=sys.argv[1].rsplit('/', 1)[0])))
PY
    ;;
  commit)
    REPO=$G/commits/$ID
    [ -d "$REPO" ] || { echo "no code tree $REPO"; exit 1; }
    DATA=${DATA:-$(python3 -c "
import json; a = json.load(open('$F/aliases.json')); m = json.load(open('$F/revisions/' + a['train5'] + '/revision.json'))
print(json.load(open('$F/revisions/' + m['base_revision'] + '/revision.json'))['data_root'])")}
    OUT=$G/out/$ID; mkdir -p "$OUT"; rm -rf "${OUT:?}/$TILE" "$G/work/$ID/$TILE"
    log=$G/logs/helper-$ID-$TILE.log
    if REPO=$REPO TURKU3D_DATA=$DATA TURKU3D_SHARED_CACHE=$F/cache TURKU3D_WORK=$G/work/$ID/$TILE \
       TURKU3D_THREADS_PER_WORKER=$(( $(nproc) / ${SLOTS:-4} )) \
       "$T/shared-tiles-run.sh" python -m turku3d.coarse build "$TILE" --out "$OUT/$TILE" > "$log" 2>&1; then
      echo "{\"tile\": \"$TILE\", \"state\": \"built\", \"commit\": \"$ID\", \"wall\": $(( $(date +%s) - t0 )), \"path\": \"$OUT/$TILE\"}"
    else
      echo "{\"tile\": \"$TILE\", \"state\": \"failed\", \"commit\": \"$ID\", \"log\": \"$log\", \"tail\": $(tail -5 "$log" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}"
      exit 1
    fi ;;
  *) sed -n 2,8p "$0"; exit 2 ;;
esac
