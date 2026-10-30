#!/usr/bin/env bash
# On OCI: request 8 random bulk-built tiles through the public URL and report any rebuild the producer starts.
now=$(date +%s)
mapfile -t tiles < <(shuf -n 8 ~/bulk-warmup-20261030/bulk-pulled.txt)
for t in "${tiles[@]}"; do curl -s -o /dev/null -w "$t %{http_code}\n" "https://turku.lab.madekivi.fi/coarse/$t/manifest.json?d=3000"; done
sleep 40
pat=$(IFS='|'; echo "${tiles[*]}")
tail -c 3000000 ~/turku3d-drafts/reports/service.log | grep -a '"job"' | grep -a -E "\"($pat)\"" | python3 -c "
import sys,json
for r in map(json.loads,sys.stdin):
    if r['at']>$now: print('REBUILT',r['job'],r['pass'],r['outcome'])
print('check done')"
