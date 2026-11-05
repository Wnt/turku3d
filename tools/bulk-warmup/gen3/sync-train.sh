#!/bin/bash
# osgallery-dev: bring a migrated alias up to date on the bulk host right before its window, and pull it back after.
# Read-only here; nice 19, ionice idle; resumable. The host is the one in ~/turku3d-local/loops/bulkgen3/server.json.
#   sync-train.sh ALIAS          -> code snapshot, every revision.json, aliases.json, and the products resolve.py
#                                   names (base, spot rebuilds, checkpoints, finished tiles), mirrored with --delete:
#                                   the farm rebuilds spot-rebuild tiles in place and builds missing base tiles
#   sync-train.sh ALIAS --pull   -> after (or during) the window: new files of the alias's generations, hard-linked to
#                                   the base. Each tile's ready.json goes in a second rsync, after all its other files, so
#                                   the farm and city.lab's follow chain never serve a tile whose files are still arriving.
# A cached host (host-cache.sh: the farm mounted from here through FS-Cache, writes in an overlay) needs no pre-window
# sync: it reads this farm live. --pull then reads only the overlay's upper layer (/srv/farm/upper), the host's own
# writes, and skips whiteouts (device files) and anything already here.
set -euo pipefail
A=${1:?alias}; MODE=${2:-}
HERE=$(cd "$(dirname "$0")" && pwd)
D=$HOME/turku3d-local/loops/bulkgen3
F=/home/wnt/turku3d-local/loops/tiles/farm
IP=$(python3 -c "import json; print(json.load(open('$D/server.json'))['ip'])")
H=root@$IP
export RSYNC_RSH="ssh -i $HOME/.ssh/bulk_gen3 -c aes128-gcm@openssh.com -o ServerAliveInterval=30"
# nice 5: at load ~100 here a nice-19 receiver got almost no CPU (1 MB/s pulls); ZFS ignores ionice.
R="nice -n5 rsync -aH --numeric-ids --partial-dir=.rsync-partial"
python3 "$HERE/resolve.py" --farm-root $F "$A" > "$D/resolved-$A.json"
field() { python3 -c "import json; d=json.load(open('$D/resolved-$A.json')); print($1)"; }
REV=$(field "d['aliases']['$A']['revision']"); CODE=$(field "d['aliases']['$A']['code']")
GEN=$(field "d['aliases']['$A']['generation']"); BASE=$(field "d['aliases']['$A']['base_generation']")
mapfile -t GENS < <(field "'\n'.join(d['products'])")
cd $F
if [ "$MODE" = --pull ]; then
  # The host's farm is either a cached overlay (its own writes in /srv/farm/upper) or a staged local tree ($F).
  SRC=$F
  [ "$(ssh -i "$HOME/.ssh/bulk_gen3" "$H" "findmnt -no FSTYPE $F" 2>/dev/null)" = overlay ] && SRC=/srv/farm/upper
  P="$R --no-D --ignore-existing --link-dest=$F/products/$BASE/"
  # The alias's own, step and spot-rebuild generations, whether or not they exist here yet (resolve.py lists only
  # generations present on this farm).
  mapfile -t MORE < <(python3 -c "
import json; m = json.load(open('$F/revisions/$REV/revision.json'))
print('\n'.join([m['generation']] + [s[k] for s in m['migrations'] for k in ('generation', 'rebuild_generation') if s.get(k)]))")
  pull_gen() {  # finished tiles only (ready.json on the host), files first and ready.json last; shared/ etc. merged
    local g=$1 list
    list=$(mktemp)
    ssh -i "$HOME/.ssh/bulk_gen3" "$H" "cd $SRC/products/$g 2>/dev/null && ls -d */ready.json 2>/dev/null" |
      sed 's|/ready.json$||' | while read -r t; do [ -f "products/$g/$t/ready.json" ] || echo "$t"; done > "$list"
    mkdir -p "products/$g"
    { $P --exclude='t0_*' "$H:$SRC/products/$g/" "products/$g/" || [ $? = 24 ]; }
    if [ -s "$list" ]; then
      { $P -r --files-from="$list" --exclude=ready.json --exclude='*.tmp' "$H:$SRC/products/$g/" "products/$g/" || [ $? = 24 ]; } &&
        sed 's|$|/ready.json|' "$list" > "$list.r" &&
        { $P --files-from="$list.r" "$H:$SRC/products/$g/" "products/$g/" || [ $? = 24 ]; } &&
        echo "pulled products/$g ($(wc -l < "$list") ready tiles)"
    fi
    rm -f "$list" "$list.r"
  }
  for g in $(printf '%s\n' "${GENS[@]}" "$GEN" "${MORE[@]}" | sort -u); do
    [ "$g" = "$BASE" ] && continue
    pull_gen "$g" &
  done
  wait
  exit 0
fi
$R -R --include='/revisions/' --include='/revisions/*/' --include='/revisions/*/*.json' --exclude='*' ./ "$H:$F/"
$R --delete -R aliases.json "revisions/$REV/" "revisions/$CODE/" "${GENS[@]/#/products/}" "$H:$F/"
echo "{\"alias\": \"$A\", \"revision\": \"$REV\", \"code\": \"$CODE\", \"generation\": \"$GEN\", \"products\": ${#GENS[@]}}"
