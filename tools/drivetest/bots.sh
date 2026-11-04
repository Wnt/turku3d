#!/bin/bash
# Delivery bots on the live city.lab multiplayer server (game/tests/drive/deliveries.ts), run on osgallery-dev next to
# city-mp (ws://127.0.0.1:7782, Origin https://city.lab.madekivi.fi) and the tile farm. No browser.
#   tools/drivetest/bots.sh start [N]   sync this checkout there and start N bots (default 5)
#   tools/drivetest/bots.sh stop        stop them (they leave the room)
#   tools/drivetest/bots.sh status      one status line (and BOTS_VERBOSE=1 for every bot)
set -euo pipefail
HOST=osgallery-dev
R=/home/wnt/turku3d-local/autopilot
REPO=$(cd "$(dirname "$0")/../.." && pwd)
case "${1:-status}" in
start)
  n=${2:-5}
  ssh $HOST "touch $R/bots.stop; sleep 2" # a running set leaves first
  (cd "$REPO" && git archive HEAD) | ssh $HOST "mkdir -p $R/bots-repo && cd $R/bots-repo && find . -mindepth 1 -maxdepth 1 ! -name game ! -name server -exec rm -rf {} + ; \
    mkdir -p game && find game -mindepth 1 -maxdepth 1 ! -name node_modules -exec rm -rf {} + ; tar -xf - && \
    ln -sfn /home/wnt/turku3d-local/loops/integration/checks/deps/a4c110cf7a0b0700/node_modules game/node_modules && \
    (ls server/node_modules >/dev/null 2>&1 || (cd server && npm ci --ignore-scripts --no-audit --no-fund >/dev/null 2>&1))"
  ssh $HOST "mkdir -p $R/sweep && cd $R/sweep && cp -n ~/turku3d-local/city/tiles.json /dev/null 2>/dev/null; \
    [ -s tile-service.json ] && [ -s navigation.json ] || curl -s -m 60 -o navigation.json \"\$(python3 -c 'import json;c=json.load(open(\"tile-service.json\"));print(c[\"service\"]+\"/v/\"+c[\"version\"]+\"/coarse/navigation.json\")')\"; \
    rm -f $R/bots.stop; cd $R/bots-repo/game && (BOTS_URL=ws://127.0.0.1:7782/ BOTS_ORIGIN=https://city.lab.madekivi.fi DRIVE_DIR=$R/sweep BOTS_N=$n \
    BOTS_STATUS=$R/bots-status.json BOTS_STOP=$R/bots.stop nohup nice -n 5 npx vitest run -c vitest.drive.config.ts tests/drive/deliveries.test.ts > $R/bots.log 2>&1 &)"
  echo "starting $n bots on $HOST (status: $0 status)" ;;
stop)
  ssh $HOST "touch $R/bots.stop"; echo "bots asked to leave" ;;
status)
  ssh $HOST "cat $R/bots-status.json 2>/dev/null" | python3 -c "
import json,sys,os
try: s=json.load(sys.stdin)
except Exception: print('no status yet'); sys.exit()
print(f\"{s['t'][:19]}Z up {s['uptimeS']}s: {s['connected']}/{s['bots']} bots connected, {s['deliveries']} deliveries, {s['stuck']} stuck, {s['respawns']} respawns\")
if os.environ.get('BOTS_VERBOSE'):
  for b in s['list']: print(f\"  {b['nick']:16} {b['state']:8} -> {str(b['to'])[:36]:36} {b['speedKmh']} km/h  deliveries {b['deliveries']} stuck {b['stuck']} at {b['at']}\")" ;;
*) echo "usage: $0 start [N] | stop | status"; exit 2 ;;
esac
