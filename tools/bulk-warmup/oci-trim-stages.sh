#!/usr/bin/env bash
# On OCI: every 10 min, drop stage caches of tiles whose current-generator full build finished >10 min ago.
# Stop with: rm ~/trim-stages.on
touch ~/trim-stages.on
while [ -f ~/trim-stages.on ]; do
  curl -s http://127.0.0.1:7764/coarse/health > ~/bulk-warmup-20261030/trim-health.json
  python3 - > ~/bulk-warmup-20261030/trim-now.txt <<'PY'
import glob, json, os, time
h = json.load(open(os.path.expanduser("~/bulk-warmup-20261030/trim-health.json")))
code = h["prebuild"]["generator"]
# Tiles that may still be rebuilt keep their stage caches: building now, or bulk-built with a stale input key.
busy = set(h["prebuild"]["building"])
stale_list = os.path.expanduser("~/bulk-warmup-20261030/stale-inputs.json")
stale = set(json.load(open(stale_list))) if os.path.exists(stale_list) else set()
for m in glob.glob("/srv/turku3d/work/coarse/t0_*/manifest.json"):
    tile = m.split("/")[-2]
    if tile in busy or tile in stale:
        continue
    try:
        d = json.load(open(m))
        if d.get("code") == code and d.get("pass_kind") == "full" and time.time() - os.path.getmtime(m) > 600:
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
