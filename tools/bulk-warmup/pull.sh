#!/usr/bin/env bash
# Run on OCI as ubuntu: pull finished tiles from the bulk host and swap each into the tile store
# atomically. Shared textures go first: tiles reference them. BULK_HOST=root@<ip>.
set -uo pipefail
H=${BULK_HOST:?BULK_HOST=root@ip}
B=/root/turku3d-bulk-warmup
E="ssh -c aes128-gcm@openssh.com -i $HOME/.ssh/bulk_upcloud -o IdentitiesOnly=yes"
STORE=/srv/turku3d/work/coarse
SEEN=$HOME/bulk-pulled.txt
touch "$SEEN"
while true; do
  $E $H "cat $B/state/bulk-progress.jsonl 2>/dev/null" | python3 -c '
import sys,json
seen=set(open(sys.argv[1]).read().split())
for l in sys.stdin:
    r=json.loads(l)
    if r["outcome"]=="built" and r["tile"] not in seen: print(r["tile"])' "$SEEN" > ~/bulk-new.txt
  [ -s ~/bulk-new.txt ] && rsync -a --ignore-existing -e "$E" "$H:$B/work/coarse/shared/" "$STORE/shared/"
  for t in $(cat ~/bulk-new.txt); do
    tmp="$STORE/.bulk-$t"
    rm -rf "$STORE/.bulk-$t"
    if rsync -a -e "$E" "$H:$B/work/coarse/$t/" "$tmp/"; then
      chmod 750 "$tmp"
      if [ -d "$STORE/$t" ]; then mv "$STORE/$t" "$STORE/.old-$t"; fi
      mv "$tmp" "$STORE/$t" && rm -rf "$STORE/.old-$t"
      echo "$t" >> "$SEEN"
    fi
  done
  if $E $H "test -f $B/state/ALLDONE"; then
    n=$($E $H "grep -c built $B/state/bulk-progress.jsonl"); m=$(sort -u "$SEEN" | wc -l)
    [ "$m" -ge "$n" ] && { echo "pull complete $m tiles"; break; }
  fi
  sleep 10
done
