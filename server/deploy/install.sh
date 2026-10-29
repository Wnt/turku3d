#!/usr/bin/env bash
# Install user units without starting a second public tunnel during migration.
set +x
set -euo pipefail
umask 077
with_drafts=false
if [[ ${1:-} == --drafts ]]; then
  with_drafts=true
  shift
fi
if (($#)); then
  echo 'Usage: server/deploy/install.sh [--drafts]' >&2
  exit 1
fi
repo_dir="$HOME/turku3d-mp/repo"
draft_home="$HOME/turku3d-drafts"
unit_source="$(cd "$(dirname "$0")" && pwd)"
unit_dir="$HOME/.config/systemd/user"
node_bin="$(command -v node)"
[[ $(node -p 'process.versions.node.split(".")[0]') == 24 ]]
test -x "$HOME/turku3d-mp/bin/forwarder-agent"
test -f "$HOME/.config/turku3d/forwarder.env"
chmod 600 "$HOME/.config/turku3d/forwarder.env"
mkdir -p "$unit_dir" "$unit_dir/turku-mp-server.service.d"
npm ci --prefix "$repo_dir/server" --ignore-scripts --omit=dev --no-fund
install -m 644 "$unit_source/"*.service "$unit_dir/"
# Node may be the private ARM64 distribution rather than /usr/bin/node.
printf '[Service]\nExecStart=\nExecStart="%s" server/index.js\n' "$node_bin" \
  >"$unit_dir/turku-mp-server.service.d/node.conf"
if "$with_drafts"; then
  test -x "$draft_home/tools/bin/caddy"
  test -d "$draft_home/data/raw"
  mkdir -p "$draft_home/reports" "$unit_dir/turku-forwarder-agent.service.d" \
    "$unit_dir/turku-drafts.service.d"
  install -m 644 "$draft_home/repo/tools/remote-drafts/"*.service "$unit_dir/"
  cat >"$unit_dir/turku-forwarder-agent.service.d/remote-drafts.conf" <<'UNIT'
[Unit]
After=turku-drafts-proxy.service
Requires=turku-drafts-proxy.service
[Service]
ExecStart=
ExecStart=%h/turku3d-mp/bin/forwarder-agent --server tunnel.lab.madekivi.fi --tunnel turku:http:turku.lab.madekivi.fi:7764
UNIT
  # User managers cannot pull in system mounts. Fail closed if a volume is absent;
  # Restart retries after boot until its automount is ready. Never fill the boot disk.
  data_root=$(readlink -f "$draft_home/data")
  work_root=$(readlink -f "$draft_home/work")
  printf '[Service]\nExecStartPre=/usr/bin/test -d "%s/raw"\nExecStartPre=/usr/bin/test -d "%s"\n' \
    "$data_root" "$work_root" >"$unit_dir/turku-drafts.service.d/storage.conf"
fi
systemctl --user daemon-reload
echo 'Units installed. Start local services and verify them before enabling the tunnel.'
