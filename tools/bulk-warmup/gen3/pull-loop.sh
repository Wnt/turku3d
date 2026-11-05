#!/bin/bash
# osgallery-dev: bring the bulk host's finished tiles home, during and after a window.
#   pull-loop.sh ALIAS [--once] [--every S]     (default every 240 s until stopped; nice 19 / ionice idle)
# Turku: sync-train.sh ALIAS --pull (the alias's generations from the host overlay's upper layer, ready.json last).
# Other cities: each tile the host's window-cities.py logged as built (cities/<city>/.../window-builds.jsonl) is
# fetched into OUT/.incoming-TILE (hard-linked against the current copy) and swapped in with two renames, so the
# city's tile service sees either the old tile or the complete new one; shared/ (Espoo's textures) is merged first.
# Log: ~/turku3d-local/loops/bulkgen3/pull.log (one line per round: tiles, bytes, seconds).
set -uo pipefail
A=${1:?alias}; shift
ONCE=; EVERY=240
while [ $# -gt 0 ]; do case "$1" in --once) ONCE=1; shift ;; --every) EVERY=$2; shift 2 ;; *) exit 2 ;; esac; done
HERE=$(cd "$(dirname "$0")" && pwd)
D=$HOME/turku3d-local/loops/bulkgen3
C=/home/wnt/turku3d-local/cities
IP=$(python3 -c "import json; print(json.load(open('$D/server.json'))['ip'])")
H=root@$IP
SSH="ssh -i $HOME/.ssh/bulk_gen3 -o BatchMode=yes -o ConnectTimeout=10"
export RSYNC_RSH="$SSH -c aes128-gcm@openssh.com -o ServerAliveInterval=30"
R="nice -n19 ionice -c3 rsync -aH --numeric-ids --no-D"
round() {
  local t0 n=0 b0 b1
  t0=$(date +%s); b0=$(cat /sys/class/net/eth0/statistics/rx_bytes)
  "$HERE/sync-train.sh" "$A" --pull > "$D/pull-turku.out" 2>&1
  for out in "$C"/espoo/tiles "$C"/amsterdam/work/coarse "$C"/rotterdam/work/coarse; do
    log=$(dirname "$out")/window-builds.jsonl
    $SSH "$H" "test -f $log" 2>/dev/null || continue
    mkdir -p "$out"
    [ "${out##*/}" = tiles ] && $R "$H:$out/shared/" "$out/shared/" 2>/dev/null
    while read -r tile at; do
      # The marker holds the build time: a newer build of the same tile (e.g. on a frozen checkout) comes home again.
      [ "$(cat "$out/.pulled-$tile" 2>/dev/null)" = "$at" ] && continue
      link=(); [ -d "$out/$tile" ] && link=(--link-dest="$out/$tile/")
      $R "${link[@]}" "$H:$out/$tile/" "$out/.incoming-$tile/" || continue
      [ -d "$out/$tile" ] && mv "$out/$tile" "$out/.old-$tile"
      mv "$out/.incoming-$tile" "$out/$tile" && rm -rf "$out/.old-$tile" && echo "$at" > "$out/.pulled-$tile" && n=$((n + 1))
    done < <($SSH "$H" "python3 -c \"import json,sys; last = {r['tile']: r['at'] for r in map(json.loads, open('$log')) if r['state'] == 'built'}; [print(t, a) for t, a in last.items()]\"")
  done
  b1=$(cat /sys/class/net/eth0/statistics/rx_bytes)
  echo "$(date -u +%FT%TZ) turku: $(grep -c pulled "$D/pull-turku.out") generations; cities: $n tiles; $(( (b1 - b0) / 1000000 )) MB in $(( $(date +%s) - t0 )) s" >> "$D/pull.log"
}
while true; do
  round
  [ -n "$ONCE" ] && break
  sleep "$EVERY"
done
tail -1 "$D/pull.log"
