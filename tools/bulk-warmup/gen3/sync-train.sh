#!/bin/bash
# osgallery-dev: bring a migrated alias up to date on the bulk host right before its window, and pull it back after.
# Read-only here; nice 19, ionice idle; resumable. The host is the one in ~/turku3d-local/loops/bulkgen3/server.json.
#   sync-train.sh ALIAS          -> code snapshot, every revision.json, aliases.json, and the products resolve.py
#                                   names (base, spot rebuilds, checkpoints, finished tiles), mirrored with --delete:
#                                   the farm rebuilds spot-rebuild tiles in place and builds missing base tiles
#   sync-train.sh ALIAS --pull   -> after the window: new files of the alias's generations, hard-linked to the base
set -euo pipefail
A=${1:?alias}; MODE=${2:-}
HERE=$(cd "$(dirname "$0")" && pwd)
D=$HOME/turku3d-local/loops/bulkgen3
F=/home/wnt/turku3d-local/loops/tiles/farm
IP=$(python3 -c "import json; print(json.load(open('$D/server.json'))['ip'])")
H=root@$IP
export RSYNC_RSH="ssh -i $HOME/.ssh/bulk_gen3 -c aes128-gcm@openssh.com -o ServerAliveInterval=30"
R="nice -n19 ionice -c3 rsync -aH --numeric-ids --partial-dir=.rsync-partial"
python3 "$HERE/resolve.py" --farm-root $F "$A" > "$D/resolved-$A.json"
field() { python3 -c "import json; d=json.load(open('$D/resolved-$A.json')); print($1)"; }
REV=$(field "d['aliases']['$A']['revision']"); CODE=$(field "d['aliases']['$A']['code']")
GEN=$(field "d['aliases']['$A']['generation']"); BASE=$(field "d['aliases']['$A']['base_generation']")
mapfile -t GENS < <(field "'\n'.join(d['products'])")
cd $F
if [ "$MODE" = --pull ]; then
  for g in "${GENS[@]}" "$GEN"; do
    [ "$g" = "$BASE" ] && continue
    ssh -i "$HOME/.ssh/bulk_gen3" "$H" test -d "$F/products/$g" || continue
    $R --ignore-existing --link-dest="$F/products/$BASE/" "$H:$F/products/$g/" "products/$g/" && echo "pulled products/$g"
  done
  exit 0
fi
$R -R --include='/revisions/' --include='/revisions/*/' --include='/revisions/*/*.json' --exclude='*' ./ "$H:$F/"
$R --delete -R aliases.json "revisions/$REV/" "revisions/$CODE/" "${GENS[@]/#/products/}" "$H:$F/"
echo "{\"alias\": \"$A\", \"revision\": \"$REV\", \"code\": \"$CODE\", \"generation\": \"$GEN\", \"products\": ${#GENS[@]}}"
