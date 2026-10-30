#!/usr/bin/env bash
# Run ON a bulk host that can SSH to OCI: push each finished tile (shared textures first) into the
# OCI tile store and swap it in atomically on OCI. Counterpart of pull.sh for hosts OCI cannot reach.
set -uo pipefail
BULK_ROOT=${BULK_ROOT:-$HOME/turku3d-bulk-warmup}
OCI=${OCI:-ubuntu@82.70.50.61}
KEY=${OCI_KEY:-$HOME/.ssh/oci_cuttlefish}
E="ssh -c aes128-gcm@openssh.com -i $KEY -o IdentitiesOnly=yes"
STORE=/srv/turku3d/work/coarse
SEEN=$BULK_ROOT/state/pushed.txt
touch "$SEEN"
while true; do
  python3 -c '
import json,sys
seen=set(open(sys.argv[1]).read().split())
for l in open(sys.argv[2]):
    r=json.loads(l)
    if r["outcome"]=="built" and r["tile"] not in seen: print(r["tile"])' "$SEEN" "$BULK_ROOT/state/bulk-progress.jsonl" > "$BULK_ROOT/state/push-new.txt" 2>/dev/null
  if [ -s "$BULK_ROOT/state/push-new.txt" ]; then
    rsync -a --ignore-existing -e "$E" "$BULK_ROOT/work/coarse/shared/" "$OCI:$STORE/shared/"
    for t in $(grep -E '^t0_-?[0-9]+_-?[0-9]+$' "$BULK_ROOT/state/push-new.txt"); do
      if rsync -a --chmod=D750 -e "$E" "$BULK_ROOT/work/coarse/$t/" "$OCI:$STORE/.bulk-$t/"; then
        $E "$OCI" "cd $STORE && { [ -d $t ] && mv $t .old-$t; mv .bulk-$t $t && rm -rf .old-$t; }" && echo "$t" >> "$SEEN"
      fi
    done
  fi
  [ -f "$BULK_ROOT/state/ALLDONE" ] && [ ! -s "$BULK_ROOT/state/push-new.txt" ] && { echo "push complete"; break; }
  sleep 15
done
