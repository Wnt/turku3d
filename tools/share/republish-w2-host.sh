#!/usr/bin/env bash
# The host half of tools/share/republish-w2.sh: runs on osgallery-dev through scripts/dev/on-host, in a checkout of
# local/loop-integration, and builds every location the lab lists again with that code and the stored readings.
# At most BUILDS (3) location builds run at once: the host allows six and other tracks build there too.
#
# The list is the published index (~/turku3d-local/city/world/index.json). A pilot area is built by its name (its
# query comes from the reference views), any other location from the query its manifest records. The first run
# starts from this track's earlier store by hard links, so nothing that is unchanged compiles again. A build that
# fails keeps the location's previous manifest; its log says why. Exit 1 when any build failed.
#
#   bash tools/share/republish-w2-host.sh [NAME | NEW=OLD]...   only these (default: all the lab lists); NEW=OLD builds
#                                                               NEW from the query of OLD
set -uo pipefail
BUILDS=${BUILDS:-3}
INDEX=$HOME/turku3d-local/city/world/index.json
SEED=$HOME/turku3d-w2/w2-play/work/world
STORE=$TURKU3D_WORK/world
LOGS=$TURKU3D_WORK/republish
mkdir -p "$LOGS"
if [ ! -d "$STORE" ] && [ -d "$SEED" ]; then
  cp -al "$SEED" "$STORE"
  rm -rf "$STORE/building"
  echo "republish: store seeded from $SEED by hard links"
fi
free=$(df -Pk "$HOME" | awk 'NR==2 { printf "%d", $4 / 1048576 }')
# A round whose generators changed stores up to a few GiB of new meshes: stop short of the host's 25 GiB floor.
if [ "$free" -lt 29 ]; then
  echo "republish: only $free GiB free on the host (floor 25): no builds. Free space: python -m turku3d.world gc in $STORE, then the unreferenced objects of ~/turku3d-local/city/world"
  exit 3
fi
python - "$INDEX" "$@" > "$LOGS/plan.txt" <<'PY'
import json, sys
from pathlib import Path
areas = set()
for line in Path('qa/views/views.jsonl').read_text().splitlines():
    if line.strip():
        areas.add(json.loads(line).get('area'))
# NEW=OLD builds location NEW from the query of the listed location OLD (a copy under another name, when OLD's
# published build is evidence that must stay as it is).
asked = dict(arg.split('=', 1) if '=' in arg else (arg, arg) for arg in sys.argv[2:])
listed = {row['name']: row for row in json.load(open(sys.argv[1]))['locations']}
rows = [dict(listed[old], name=new) for new, old in asked.items() if old in listed] if asked else list(listed.values())
# The pilot areas first: they are what a visitor opens first, and what is judged again.
for row in sorted(rows, key=lambda row: (row['name'] not in areas, row['name'])):
    query, name = row['query'], row['name']
    if name in areas:
        print(name, '--area', name)
    else:
        print(name, '--at', f"{query['at'][0]},{query['at'][1]}", '--radius', query['radius'], '--background', query['background'], '--name', name)
PY
echo "republish: $(wc -l < "$LOGS/plan.txt") locations, $BUILDS at once, $free GiB free"
[ -s "$LOGS/plan.txt" ] || { echo "republish: nothing to build (no listed location matches the arguments)"; exit 2; }
export LOGS
build() {
  local name=$1; shift
  local started=$SECONDS
  if python -m turku3d.world build "$@" > "$LOGS/$name.log" 2>&1; then
    echo "republish: built $name in $((SECONDS - started)) s: $(grep -m1 '^  total' "$LOGS/$name.log" | sed 's/  */ /g')"
  else
    echo "republish: FAILED $name after $((SECONDS - started)) s: $(tail -1 "$LOGS/$name.log")"
    return 1
  fi
}
export -f build
# xargs returns 123 when any build failed; every other build still runs.
xargs -r -P "$BUILDS" -L 1 bash -c 'build "$@"' _ < "$LOGS/plan.txt"
code=$?
grep -l 'FAILED' "$LOGS"/*.log 2>/dev/null | while read -r log; do echo "republish: failures in $(basename "$log" .log): $(grep -c FAILED "$log")"; done
[ "$code" = 0 ] || exit 1
