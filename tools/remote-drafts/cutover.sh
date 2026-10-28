#!/usr/bin/env bash
# Run on the old host after warming and checking the destination's local services.
set +x
set -euo pipefail
destination=${1:?Usage: cutover.sh SSH_DESTINATION UNCACHED_TILE}
tile=${2:?Pass a tile absent from the destination serving cache}
repo=$(cd "$(dirname "$0")/../.." && pwd)
mp_data="$HOME/turku3d-mp/repo/server/data/"
ssh "$destination" 'test -f ~/.config/turku3d/forwarder.env; systemctl --user is-active turku-drafts turku-drafts-proxy'
# systemctl disable removes linked unit files as well as enablement links.
# Retain ordinary unit files so the source stays ready for rollback.
for unit in turku-forwarder-agent turku-mp-server turku-drafts-proxy turku-drafts; do
  unit_file="$HOME/.config/systemd/user/$unit.service"
  if [[ -L $unit_file ]]; then
    install -m 644 "$(readlink -f "$unit_file")" "$unit_file.retained"
    mv -f "$unit_file.retained" "$unit_file"
  fi
done
systemctl --user daemon-reload
rollback() {
  echo 'Cutover verification failed; restoring the source tunnel.' >&2
  ssh "$destination" 'systemctl --user disable --now turku-forwarder-agent; systemctl --user stop turku-mp-server' || true
  rsync -a "$destination:turku3d-mp/repo/server/data/" "$mp_data" || true
  systemctl --user enable --now turku-mp-server turku-drafts turku-drafts-proxy turku-forwarder-agent
}
trap rollback ERR
# Stopping multiplayer freezes score writes; stop the source tunnel before starting its replacement.
systemctl --user stop turku-forwarder-agent turku-mp-server
ssh "$destination" 'systemctl --user stop turku-mp-server'
rsync -a --delete "$mp_data" "$destination:turku3d-mp/repo/server/data/"
ssh "$destination" 'systemctl --user start turku-mp-server; systemctl --user enable --now turku-forwarder-agent'
curl -fsS --retry 10 --retry-all-errors --retry-delay 1 --max-time 10 \
  -H 'Origin: https://wnt.github.io' https://turku.lab.madekivi.fi/coarse/health
node "$repo/server/tools/probe-presence.mjs"
python3 "$repo/tools/remote-drafts/probe.py" t0_1_2
python3 "$repo/tools/remote-drafts/probe.py" "$tile"
systemctl --user disable --now turku-forwarder-agent turku-mp-server turku-drafts-proxy turku-drafts
trap - ERR
echo 'Cutover verified; source services disabled. Retain source files and data for seven days.'
