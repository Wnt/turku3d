#!/bin/bash
# Mac, read-only, about 2-4 s: one compact status block of the bulk window run (window-stat.py on the host) plus the
# pull-back backlog (tiles finished on the host but not yet on osgallery-dev). Prints "no window" when none runs.
G=${BULK_STATE:-$HOME/turku3d/bulk-cache}
OSG=osgallery-dev
conf() { python3 -c "import json; print(json.load(open('$G/server.json'))['$1'])" 2>/dev/null; }
IP=$(conf ip); [ -n "$IP" ] || { echo "no window (no host)"; exit 0; }
O="-o ConnectTimeout=3 -o BatchMode=yes"
out=$(mktemp); back=$(mktemp)
timeout 4 ssh $O "root@$IP" 'python3 /home/wnt/turku3d-bulk-gen3/window-stat.py; G=/home/wnt/turku3d-bulk-gen3
  python3 -c "import json; print(json.load(open(\"$G/window.json\"))[\"revision\"])" 2>/dev/null; cat $G/window.started 2>/dev/null' > "$out" 2>/dev/null &
wait
if [ ! -s "$out" ]; then echo "no window (host $IP not reachable: off or booting)"; rm -f "$out" "$back"; exit 0; fi
if [ "$(head -1 "$out")" = "no window" ]; then echo "no window"; rm -f "$out" "$back"; exit 0; fi
rev=$(tail -2 "$out" | head -1); since=$(tail -1 "$out")
timeout 3 ssh $O $OSG "F=~/turku3d-local/loops/tiles/farm; g=\$(python3 -c \"import json; print(json.load(open('\$F/revisions/$rev/revision.json'))['generation'])\")
  find \$F/products/\$g -maxdepth 2 -name ready.json -newermt @$since 2>/dev/null | wc -l
  ls -a ~/turku3d-local/cities/*/tiles ~/turku3d-local/cities/*/work/coarse 2>/dev/null | grep -c '^\.pulled-'
  echo \"\$(tail -1 ~/turku3d-local/loops/bulkgen3/pull.log 2>/dev/null | cut -c1-90); current round started \$(date -u -r ~/turku3d-local/loops/bulkgen3/pull-turku.out +%H:%M:%S 2>/dev/null) UTC, \$(grep -c '^pulled' ~/turku3d-local/loops/bulkgen3/pull-turku.out) generations done\"" > "$back" 2>/dev/null
n=$(wc -l < "$out"); body=$(awk -v n="$n" 'NR <= n - 2' "$out")
echo "$body"
built=$(echo "$body" | tail -1)
t=$(echo "$built" | sed -n 's/.*turku \([0-9]*\) ready.*/\1/p'); c=$(echo "$built" | sed -n 's/.*cities \([0-9]*\).*/\1/p')
if [ -s "$back" ]; then
  pt=$(sed -n 1p "$back"); pc=$(sed -n 2p "$back")
  echo "pull backlog: turku $(( ${t:-0} - ${pt:-0} )), cities $(( ${c:-0} - ${pc:-0} )) | last pull: $(sed -n 3p "$back")"
else echo "pull backlog: osgallery-dev not reachable"; fi
rm -f "$out" "$back"
