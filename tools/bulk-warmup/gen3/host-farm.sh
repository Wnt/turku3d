#!/bin/bash
# Bulk host: the farm service (osgallery-dev's deployed shared_tiles.py) on 127.0.0.1:7950 against the cached farm
# overlay, so a pass's spot-rebuild requests (Rebuild steps such as bridge-ends@1 and landmark-windows@1) build here
# with the farm's own code instead of waiting for osgallery-dev. It serves nothing outside the host.
#   host-farm.sh start [WORKERS]   (default 1; MAX 12) background, as wnt; prints the pid
#   host-farm.sh stop | status
# Before it starts, the overlay's pending.json is emptied (the farm's queue is osgallery-dev's, not ours) and
# events.jsonl is a fresh local file (the farm's own is 40 MB; appending would copy it up over the tunnel).
# stage.sh cache copies shared_tiles.py and shared_sources.py from osgallery-dev's ~/turku3d-local/loops/tiles.
set -euo pipefail
[ "$(id -u)" = 1000 ] || exec su wnt -c "$(printf '%q ' "$0" "$@")"
T=/home/wnt/turku3d-local/loops/tiles
F=$T/farm
G=/home/wnt/turku3d-bulk-gen3
PID=$G/farm.pid
alive() { [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; }
case "${1:-}" in
  start)
    alive && { echo "running: $(cat "$PID")"; exit 0; }
    echo '[]' > "$F/pending.json.tmp" && mv -f "$F/pending.json.tmp" "$F/pending.json"
    rm -f "$F/events.jsonl"; : > "$F/events.jsonl"
    mkdir -p "$G/logs"
    # A background job is not a group leader, so setsid execs in place and $! is the service's own pid.
    setsid nohup python3 "$T/shared_tiles.py" --root "$F" --runner "$T/shared-tiles-run.sh" --host 127.0.0.1 \
      --port 7950 --workers "${2:-1}" >> "$G/logs/farm.log" 2>&1 < /dev/null &
    echo $! > "$PID"
    for _ in $(seq 30); do curl -sf -m2 http://127.0.0.1:7950/health > /dev/null && break; sleep 1; done
    echo "farm $(cat "$PID") on 127.0.0.1:7950, ${2:-1} workers" ;;
  stop) alive && kill -TERM "$(cat "$PID")"; rm -f "$PID" ;;
  status) alive && curl -s -m5 http://127.0.0.1:7950/health | head -c 600; echo ;;
  *) sed -n 2,9p "$0"; exit 2 ;;
esac
