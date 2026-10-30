#!/usr/bin/env bash
# Every 60 s: drop per-tile stage caches of tiles the bulk run has finished (built or failed).
while true; do
  python3 -c "import json;[print(json.loads(l)[\"tile\"]) for l in open(\"/root/turku3d-bulk-warmup/state/bulk-progress.jsonl\")]" | sort -u | grep -E "^t0_-?[0-9]+_-?[0-9]+$" | while read t; do
    if [ -d "/root/turku3d-bulk-warmup/work/coarse/.stages/$t" ]; then rm -rf "/root/turku3d-bulk-warmup/work/coarse/.stages/$t"; fi
  done
  df -h / | tail -1 >> /root/turku3d-bulk-warmup/state/disk.log
  sleep 60
done
