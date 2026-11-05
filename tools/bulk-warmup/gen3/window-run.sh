#!/bin/bash
# Bulk host (root): start, watch and stop a frozen window run. Everything comes from $G/window.json, written before
# power-on and never changed during the window (revision, city checkouts, tile queue, lane counts):
#   {"revision": "<64 hex>", "turku_lanes": 44, "max_jobs": 72, "blocks": 2, "farm_workers": 12, "city_lanes": 12,
#    "plan": "$G/cities-plan.json", "tiles_file": "$G/pass-tiles.txt"}
#   window-run.sh start     host farm (spot rebuilds), the Turku pass (pass-run.py: live lanes, lanes survive dead
#                           workers), window-warm.py, window-cities.py --follow, then the watchdog
#   window-run.sh watchdog  (started by `start`; one loop, every 5 s) samples CPU, tunnel and cache counters for
#                           window-stat.py; restarts the farm service after 60 s without health, and the pass, the
#                           cities scheduler or the warmer when their process is gone; hands idle city lanes to Turku
#                           while the city queues are empty (lanes-turku.effective = lanes-turku + idle city lanes)
#   window-run.sh stop      stop all of it (finished tiles stay; in-flight ones are lost)
# Live changes, additive only (window-lanes.sh, or a city list appended to the plan file): never a restart.
set -uo pipefail
G=/home/wnt/turku3d-bulk-gen3
F=/home/wnt/turku3d-local/loops/tiles/farm
W=$G/window.json
LOG=$G/logs/watchdog.log
cfg() { python3 -c "import json,sys; print(json.load(open('$W')).get(sys.argv[1], sys.argv[2]))" "$1" "${2:-}"; }
alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
say() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; }

start_pass() {
  LANES_FILE=$G/lanes-turku.effective MAX_JOBS=$(cfg max_jobs 72) BLOCKS=$(cfg blocks 2) HIGH=50 LOW=40 \
    TILES_FILE=$(cfg tiles_file "$G/pass-tiles.txt") WARM=0 "$G/window-pass.sh" "$(cfg revision)" >> "$LOG" 2>&1
}
start_cities() {
  su wnt -c "setsid nohup python3 $G/window-cities.py --plan $(cfg plan "$G/cities-plan.json") --lanes-file $G/lanes-cities \
    --follow >> $G/logs/cities.log 2>&1 < /dev/null & echo \$! > $G/cities.pid"
}
start_warm() {
  local rev code; rev=$(cfg revision)
  code=$(python3 -c "import json; print(json.load(open('$F/revisions/$rev/revision.json'))['code_revision'])")
  su wnt -c "cd $F/revisions/$code/repo && setsid nohup env REPO=$F/revisions/$code/repo TURKU3D_WORK=$F/work/migrate \
    /home/wnt/turku3d-local/loops/tiles/shared-tiles-run.sh python $G/window-warm.py --farm-root $F --revision $rev \
    --supply /bulk/turku3d/lidarpc/raw/pistepilvi >> $G/logs/warm.log 2>&1 < /dev/null & echo \$! > $G/warm.pid"
}

sample() {  # one line: time, cpu jiffies (user nice system idle iowait irq softirq steal), tunnel rx, cache reads, net reads
  local cpu io dl
  cpu=$(awk '/^cpu /{print $2,$3,$4,$5,$6,$7,$8,$9}' /proc/stat)
  io=$(sed -n 's/^IO *: rd=\([0-9]*\).*/\1/p' /proc/fs/fscache/stats)
  dl=$(sed -n 's/^Netfs *: DL=\([0-9]*\).*/\1/p' /proc/fs/fscache/stats)
  echo "$(date +%s) $cpu $(cat /sys/class/net/wg0/statistics/rx_bytes) ${io:-0} ${dl:-0}" >> /run/window-samples
  tail -n 40 /run/window-samples > /run/window-samples.tmp && mv /run/window-samples.tmp /run/window-samples
}

watchdog() {
  local farm_bad=0 n=0
  while true; do
    sample
    n=$((n + 1))
    if [ $((n % 2)) = 0 ]; then
      if curl -sf -m4 -o /dev/null http://127.0.0.1:7950/health; then farm_bad=0; else farm_bad=$((farm_bad + 1)); fi
      if [ "$farm_bad" -ge 6 ]; then
        say "farm unhealthy 60 s: restart"; "$G/host-farm.sh" stop; "$G/host-farm.sh" start "$(cfg farm_workers 12)" >> "$LOG" 2>&1
        farm_bad=0
      fi
      alive "$G/pass.pid" || { say "turku pass gone: restart"; start_pass; }
      alive "$G/cities.pid" || { say "cities scheduler gone: restart"; start_cities; }
      if ! alive "$G/warm.pid" && ! grep -q '"done"' "$G/logs/warm.log" 2>/dev/null && [ -f "$G/warm.pid" ]; then
        tail -1 "$G/logs/warm.log" | grep -q Traceback && { say "warmer gone: restart"; start_warm; }
      fi
      # Idle city lanes go to Turku while every city queue is empty (a city list that lands later takes them back).
      python3 - "$G" <<'PY'
import json, os, sys
g = sys.argv[1]
def num(p, d=0):
    try: return int(open(p).read().split()[0])
    except (OSError, ValueError, IndexError): return d
base, cities = num(f'{g}/lanes-turku'), num(f'{g}/lanes-cities')
try: c = json.load(open(f'{g}/cities.json'))
except (OSError, ValueError): c = {}
idle = max(0, cities - len(c.get('running', []))) if c and not sum(c.get('left', {}).values()) else 0
want = f'{base + idle}\n'
path = f'{g}/lanes-turku.effective'
if not os.path.exists(path) or open(path).read() != want:
    open(path + '.tmp', 'w').write(want); os.replace(path + '.tmp', path)
PY
    fi
    sleep 5
  done
}

case "${1:-}" in
  start)
    [ -f "$W" ] || { echo "no $W"; exit 1; }
    mkdir -p "$G/logs"; : > /run/window-samples
    cfg turku_lanes 44 > "$G/lanes-turku"; cfg city_lanes 12 > "$G/lanes-cities"; cp "$G/lanes-turku" "$G/lanes-turku.effective"
    chown 1000:1000 "$G"/lanes-* "$G/logs"
    date +%s > "$G/window.started"
    # The host's journal starts as osgallery-dev's copy: window-stat.py counts only the rows after this many.
    g=$(python3 -c "import json; print(json.load(open('$F/revisions/$(cfg revision)/revision.json'))['generation'])")
    cat "$F/migrations/$g/pass.jsonl" 2>/dev/null | wc -l > "$G/window.journal0"
    curl -sf -m2 -o /dev/null http://127.0.0.1:7950/health || "$G/host-farm.sh" start "$(cfg farm_workers 12)" >> "$LOG" 2>&1
    start_pass; start_warm; start_cities
    setsid nohup "$0" watchdog >> "$LOG" 2>&1 < /dev/null & echo $! > "$G/watchdog.pid"
    say "window started: $(cat "$W")"; echo started ;;
  watchdog) watchdog ;;
  stop)
    for p in watchdog cities warm pass; do
      alive "$G/$p.pid" && kill -TERM -- "-$(ps -o pgid= -p "$(cat "$G/$p.pid")" | tr -d ' ')" 2>/dev/null
    done
    "$G/host-farm.sh" stop; rm -f "$G/window.started"; say "window stopped"; echo stopped ;;
  *) sed -n 2,15p "$0"; exit 2 ;;
esac
