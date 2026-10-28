#!/usr/bin/env bash
# Keep the service checkout current without touching persistent scores or the tunnel.
set -euo pipefail
source_repo=${1:?Usage: update-multiplayer.sh SOURCE_REPO}
server="$HOME/turku3d-mp/repo/server"
mkdir -p "$server"
dependencies_changed=false
if ! cmp -s "$source_repo/server/package.json" "$server/package.json" ||
   ! cmp -s "$source_repo/server/package-lock.json" "$server/package-lock.json" ||
   ! test -d "$server/node_modules/ws"; then
  dependencies_changed=true
fi
# Check content, not timestamps: a new archive alone must not interrupt players.
# Exclusions also protect receiver files from --delete.
# Itemized lines starting with '.' report metadata only, not transferred content.
changes=$(rsync -rcl --delete --itemize-changes --exclude=/data/ --exclude=/node_modules/ \
  "$source_repo/server/" "$server/" | sed '/^\./d')
if "$dependencies_changed"; then
  npm ci --prefix "$server" --ignore-scripts --omit=dev --no-fund
fi
if [[ -n $changes ]]; then
  systemctl --user restart turku-mp-server
  echo 'Multiplayer source changed; service restarted.'
else
  echo 'Multiplayer source unchanged; service kept running.'
fi
# Restart is synchronous, but Node may still be opening its listener.
for attempt in {1..20}; do
  if curl --max-time 1 -fsS http://127.0.0.1:7700/healthz >/dev/null; then
    break
  fi
  sleep 0.1
done
node "$server/tools/probe-presence.mjs" ws://127.0.0.1:7764/
