#!/usr/bin/env bash
# On OCI: every 10 min, drop stage caches of tiles whose current-generator full build finished >10 min ago.
# Stop with: rm ~/trim-stages.on
touch ~/trim-stages.on
while [ -f ~/trim-stages.on ]; do
  code=$(curl -s http://127.0.0.1:7764/coarse/health | python3 -c "import sys,json;print(json.load(sys.stdin)['prebuild']['generator'])")
  python3 - "$code" > ~/bulk-warmup-20261030/trim-now.txt <<'PY'
import glob, json, os, sys, time
for m in glob.glob("/srv/turku3d/work/coarse/t0_*/manifest.json"):
    try:
        d = json.load(open(m))
        if d.get("code") == sys.argv[1] and d.get("pass_kind") == "full" and time.time() - os.path.getmtime(m) > 600:
            print(m.split("/")[-2])
    except (OSError, ValueError):
        pass
PY
  grep -E "^t0_-?[0-9]+_-?[0-9]+$" ~/bulk-warmup-20261030/trim-now.txt | while read t; do
    if [ -d "/srv/turku3d/fast-cache/coarse/.stages/$t" ]; then rm -rf "/srv/turku3d/fast-cache/coarse/.stages/$t"; fi
  done
  echo "$(date -u +%H:%M) $(df -h /srv/turku3d | awk 'NR==2{print $4" free"}')" >> ~/bulk-warmup-20261030/trim.log
  sleep 600
done
