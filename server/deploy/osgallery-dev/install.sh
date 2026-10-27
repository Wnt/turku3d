#!/usr/bin/env bash
# Idempotent user-service installation. Never run with secret-bearing shell tracing.
set +x
set -euo pipefail
umask 077
repo_dir="$HOME/turku3d-mp/repo"
bin_dir="$HOME/turku3d-mp/bin"
config_dir="$HOME/.config/turku3d"
unit_dir="$HOME/.config/systemd/user"
unit_source="$repo_dir/server/deploy/osgallery-dev"
token_source_host="${TOKEN_SOURCE_HOST:-lab}"
token_source_file="${TOKEN_SOURCE_FILE:-/etc/forwarder-agent/agent.env}"

if [[ "$(git -C "$repo_dir" branch --show-current)" != wt/mp-server ]]; then
  echo 'Install requires the wt/mp-server branch.' >&2
  exit 1
fi
if [[ "$(node -p 'process.versions.node.split(".")[0]')" != 24 ]]; then
  echo 'Install requires Node 24.' >&2
  exit 1
fi
mkdir -p "$bin_dir" "$config_dir" "$unit_dir"
chmod 700 "$config_dir"
download_dir="$(mktemp -d "$bin_dir/.forwarder-download.XXXXXX")"
token_file="$(mktemp "$config_dir/.forwarder.env.XXXXXX")"
trap 'rm -rf "$download_dir"; rm -f "$token_file"' EXIT

run_id="$(gh run list --repo Wnt/forwarder --workflow CI --branch main --status success --limit 1 --json databaseId --jq '.[0].databaseId')"
if [[ ! "$run_id" =~ ^[0-9]+$ ]]; then
  echo 'No successful Wnt/forwarder main CI artifact is available.' >&2
  exit 1
fi
gh run download "$run_id" --repo Wnt/forwarder -n forwarder-binaries --dir "$download_dir"
install -m 755 "$download_dir/forwarder-agent-linux-amd64" "$bin_dir/forwarder-agent-linux-amd64.next"
# Rename avoids ETXTBSY on repeated installation while the current binary runs.
mv -f "$bin_dir/forwarder-agent-linux-amd64.next" "$bin_dir/forwarder-agent-linux-amd64"
echo "Forwarder agent downloaded from successful CI run $run_id."

# SSH passes its command through a remote shell; quote the configurable path there too.
quoted_source_file="$(python3 -c 'import shlex, sys; print(shlex.quote(sys.argv[1]))' "$token_source_file")"
if ! ssh -o BatchMode=yes -o ConnectTimeout=10 -- "$token_source_host" \
  "grep '^FORWARDER_AGENT_TOKEN=' -- $quoted_source_file" > "$token_file"; then
  printf 'BLOCKED: ssh %s could not read FORWARDER_AGENT_TOKEN from %s. No units were changed.\n' \
    "$token_source_host" "$token_source_file" >&2
  exit 1
fi
# Validate without sourcing, evaluating or printing the secret. Preserve only the assignment.
if ! python3 - "$token_file" <<'PY'
import pathlib
import re
import sys
path = pathlib.Path(sys.argv[1])
lines = [line for line in path.read_text().splitlines() if line.startswith("FORWARDER_AGENT_TOKEN=")]
if len(lines) != 1:
    sys.exit(1)
value = lines[0].split("=", 1)[1]
if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
    value = value[1:-1]
if not re.fullmatch(r"[A-Za-z0-9_./+=:-]+", value):
    sys.exit(1)
path.write_text("FORWARDER_AGENT_TOKEN=" + value + "\n")
PY
then
  echo 'BLOCKED: the forwarder token is missing, empty, ambiguous or not a supported single-line token. No units were changed.' >&2
  exit 1
fi
chmod 600 "$token_file"
mv -f "$token_file" "$config_dir/forwarder.env"

npm ci --prefix "$repo_dir/server" --ignore-scripts --omit=dev --no-fund
install -m 644 "$unit_source/turku-mp-server.service" "$unit_dir/"
install -m 644 "$unit_source/turku-forwarder-agent.service" "$unit_dir/"
systemctl --user daemon-reload
systemctl --user enable turku-mp-server.service turku-forwarder-agent.service
systemctl --user restart turku-mp-server.service turku-forwarder-agent.service
curl -fsS --retry 12 --retry-all-errors --retry-delay 2 --connect-timeout 5 --max-time 10 \
  https://turku.lab.madekivi.fi/healthz
printf '\n'
node "$repo_dir/server/tools/probe.mjs"
