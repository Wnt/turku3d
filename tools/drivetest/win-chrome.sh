#!/bin/bash
# Chrome on the Windows host for the drive tests, under a host lease (~/turku3d/tools/host-lease/README.md).
#   tools/drivetest/win-chrome.sh start SLOTS   wait for a func lease (SLOTS windows), launch Chrome in Jonni's session
#                                               (profile C:\Research\turku3d\agents\autopilot), open the tunnels:
#                                               Mac :9362 -> Windows CDP :9232; Windows :5480/:5482 -> Mac game/server
#   tools/drivetest/win-chrome.sh stop          close that Chrome over CDP, drop the tunnel, release the lease
set -euo pipefail
WIN=jonni@192.168.1.250
L=~/turku3d/tools/host-lease/lease.py
quiet() { grep -v "post-quantum\|store now\|upgraded" || true; }
case "${1:-}" in
start)
  slots=${2:-2}
  for i in $(seq 1 120); do
    if python3 $L acquire win autopilot --kind func --slots "$slots" --ttl 40m >/dev/null; then break; fi
    [ "$i" = 1 ] && echo "win lease queued"
    [ "$i" = 120 ] && { echo "no win lease within 60 min"; exit 3; }
    sleep 30
  done
  tmp=$(mktemp -d)
  printf '@echo off\r\nstart "" "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --user-data-dir=C:\\Research\\turku3d\\agents\\autopilot\\profile --remote-debugging-port=9232 --no-first-run --no-default-browser-check --disable-background-timer-throttling --disable-renderer-backgrounding --disable-backgrounding-occluded-windows --window-size=900,560 about:blank\r\n' > "$tmp/chrome.cmd"
  ssh $WIN 'mkdir C:\Research\turku3d\agents\autopilot' 2>&1 | quiet >/dev/null || true
  scp -q "$tmp/chrome.cmd" $WIN:C:/Research/turku3d/agents/autopilot/chrome.cmd && rm -r "$tmp"
  ssh $WIN 'schtasks /create /tn T3DAutopilotChrome /sc once /st 00:00 /it /ru jonni /tr C:\Research\turku3d\agents\autopilot\chrome.cmd /f >nul 2>&1 & schtasks /run /tn T3DAutopilotChrome >nul' 2>&1 | quiet
  ssh -f -N -o ExitOnForwardFailure=yes -L 9362:127.0.0.1:9232 -R 5480:127.0.0.1:5480 -R 5482:127.0.0.1:5482 $WIN 2>&1 | quiet
  for i in $(seq 1 30); do curl -s -m 2 http://127.0.0.1:9362/json/version | grep -q Browser && { echo "win chrome up"; exit 0; }; sleep 1; done
  echo "win chrome did not come up"; exit 1 ;;
stop)
  (cd "$(dirname "$0")/../../game" && node -e "
    const { chromium } = require('@playwright/test');
    chromium.connectOverCDP('http://127.0.0.1:9362').then(async b => { const s = await b.newBrowserCDPSession(); await s.send('Browser.close').catch(() => {}); process.exit(0); }).catch(() => process.exit(0));") || true
  ssh $WIN 'schtasks /delete /tn T3DAutopilotChrome /f >nul 2>&1' 2>&1 | quiet || true
  # Only my own tunnel: the ssh whose command line carries this exact forward.
  for p in $(ps -ax -o pid=,command= | awk '/ssh -f -N -o ExitOnForwardFailure=yes -L 9362:127.0.0.1:9232/ && !/awk/ {print $1}'); do kill "$p"; done
  python3 $L release autopilot win >/dev/null || true
  echo "win chrome stopped, lease released" ;;
*) echo "usage: $0 start [SLOTS] | stop"; exit 2 ;;
esac
