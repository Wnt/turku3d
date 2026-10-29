# Multiplayer server

Node 24, ES modules, one pinned dependency (`ws`). Presence and delivery scores are
server-owned; vehicle seats/leases and bounded shared contact impulses are server-owned too; Rapier simulation stays in the game client. The exact client contract is
[PROTOCOL.md](PROTOCOL.md). World coordinates are defined by
[the architecture](../docs/ARCHITECTURE.md#d3-coordinate-system-and-origin).

From the repository root:

```sh
npm ci --prefix server --ignore-scripts
npm test --prefix server
node server/index.js
```

The server binds only `127.0.0.1:7700`. For an isolated test, set `TURKU_MP_PORT=0`
(ephemeral port) and `TURKU_MP_SCORES=/path/to/test/scores`. Production defaults to
`server/data/scores/`, ignored by Git. Completed hour files survive restart;
unfinished scores and sessions are in memory and do not. Files older than seven
days are pruned daily. The server checks delivery amounts and duplicate IDs, but
has no authoritative order lifecycle to prove an actual trip was completed.

```sh
curl -fsS http://127.0.0.1:7700/healthz
curl -fsS http://127.0.0.1:7700/live.json
curl -fsS http://127.0.0.1:7700/scores/index.json
```

Tests use small synthetic data, temporary directories and ephemeral loopback ports;
they never read city data or touch a running service. The economy test executes the
existing game's TypeScript through Node's built-in type stripping and checks its
mission payout expression. Spawn derivation and coordinate conversion are recorded
in `spawns.json`; points have 3 m spacing and were checked against the published
building footprints. Client ground snapping and obstacle handling are still needed.

Open [Turku on Pages](https://wnt.github.io/turku-game/game/) or the localhost game URL
to join the default multiplayer room. Turku is the default scene; add `?scene=dev` only
for the synthetic test world (`?mp=<websocket-url>` still overrides the multiplayer endpoint).

## Load and public probe

```sh
node server/tools/load.mjs 30
node server/tools/probe-vehicles.mjs  # isolated real-WebSocket seat/exit/late-join/transfer probe
node server/tools/probe.mjs ws://127.0.0.1:7700/
node server/tools/probe.mjs wss://turku.lab.madekivi.fi/
```

The load tool uses sandbox port base+4 with 32 sockets, 16 drivers at 20 Hz and
112 resting registry vehicles. The vehicle probe uses base+3. Both use the exported
sandbox environment; neither opens Chrome. It reports server-process CPU (100% = one core), JSON bytes/s
in/out, accepted/rejected poses and remaining connections. It uses a temporary
score directory, removes it afterwards and does not contact the public service.
Optional duration is 1–300 seconds. The probe sends hello using the allowed Pages
Origin, prints a welcome summary without its session token and disconnects. Each
probe occupies one nickname in that hour and leaves a zero-score entry.

## Deploy on Linux (OCI or osgallery-dev)

The public service runs on OCI as `ubuntu`, with the existing outbound tunnel
and loopback-only proxy. The shared installer is `server/deploy/install.sh`;
`--drafts` also installs the draft proxy, producer and supervised ring prebuild.
It requires Node 24, existing source snapshots, the native forwarder binary at
`~/turku3d-mp/bin/forwarder-agent`, and the existing token at
`~/.config/turku3d/forwarder.env` (0600). It installs without starting a tunnel,
so migration cannot accidentally run two agents with the same tunnel identity.

```sh
server/deploy/install.sh --drafts
systemctl --user enable --now turku-mp-server turku-drafts turku-drafts-proxy
node server/tools/probe-presence.mjs ws://127.0.0.1:7764/
# Stop the previous host's forwarder before enabling this one:
systemctl --user enable --now turku-forwarder-agent
node server/tools/probe-presence.mjs
```

The presence probe holds two Pages-Origin WebSockets simultaneously and checks
that the second sees both players. It never prints session tokens. Like the
single-client probe, it reserves nicknames until the next hour.

See [remote draft operations](../tools/remote-drafts/README.md) for the ARM64
bootstrap, logs, source updates, data paths, cache warmup, cutover and rollback.
`scripts/dev/deploy-drafts --commit HEAD` also checksum-syncs `server/` from the
same source snapshot into `~/turku3d-mp/repo/server/`. It deletes obsolete code,
excludes `server/data/` and `server/node_modules/`, installs dependencies when the
package manifests change, and restarts `turku-mp-server` only if server files
changed. Every deployment (including a repeated commit) runs the existing
two-client presence probe through the local proxy. Server-only changes also trigger
this OCI source deployment during `codex-task finish`. The service keeps its existing
working directory and Node override; the tunnel stays running.

Completed hourly data lives in `server/data`; unfinished-hour scores and sessions
reset on restart. The osgallery-dev files and disabled units are retained through
2026-11-04 for rollback.

Shared vehicle retention, ownership epochs, nearby deltas and clean v2 reload handling are specified in
[PROTOCOL.md](PROTOCOL.md). See the [16-player audit](../docs/reports/mp-vehicle-sync.md) for measured limits and remaining city-state work.
