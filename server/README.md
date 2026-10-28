# Multiplayer server

Node 24, ES modules, one pinned dependency (`ws`). Presence and delivery scores are
server-owned; physics stays in the later game client. The exact client contract is
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
node server/tools/load.mjs 10
node server/tools/probe.mjs ws://127.0.0.1:7700/
node server/tools/probe.mjs wss://turku.lab.madekivi.fi/
```

The load tool starts its own server on an ephemeral port and connects 32 fake
clients at 20 Hz. It reports server-process CPU (100% = one core), JSON bytes/s
in/out, accepted/rejected poses and remaining connections. It uses a temporary
score directory, removes it afterwards and does not contact the public service.
Optional duration is 1–300 seconds. The probe sends hello using the allowed Pages
Origin, prints a welcome summary without its session token and disconnects. Each
probe occupies one nickname in that hour and leaves a zero-score entry.

## Deploy on osgallery-dev

Prerequisites: `/usr/bin/node` 24, npm, Python 3, gh authenticated with access to
`Wnt/forwarder` CI artifacts, `ssh lab` able to read the shared forwarder token,
and working systemd user services with linger. No new keys or paid services.

```sh
server/deploy/osgallery-dev/install.sh
systemctl --user status turku-mp-server.service turku-forwarder-agent.service
journalctl --user -u turku-mp-server -n 100 --no-pager
journalctl --user -u turku-forwarder-agent -n 100 --no-pager
curl -fsS https://turku.lab.madekivi.fi/healthz
node server/tools/probe.mjs
```

On hosts where the user cannot read the journal, use the existing sudo access:

```sh
sudo journalctl _SYSTEMD_USER_UNIT=turku-forwarder-agent.service _UID="$(id -u)" -n 50 --no-pager
```

The idempotent installer downloads the latest successful `Wnt/forwarder` main CI
artifact into `~/turku3d-mp/bin/`, reads `FORWARDER_AGENT_TOKEN` from
`/etc/forwarder-agent/agent.env` via `ssh lab`, and copies only that assignment into
`~/.config/turku3d/forwarder.env` (0600), installs/enables/restarts both user units,
and checks public health and hello. It stops if SSH or the token is unavailable;
it never substitutes credentials or prints the token. The agent reads it via
`EnvironmentFile` and dials `tunnel.lab.madekivi.fi` with
`--tunnel turku:http:turku.lab.madekivi.fi:7700`. Only the tunnel is public.

Override the existing token source with `TOKEN_SOURCE_HOST` and `TOKEN_SOURCE_FILE`
when needed (defaults: `lab` and `/etc/forwarder-agent/agent.env`):

```sh
TOKEN_SOURCE_HOST=lab TOKEN_SOURCE_FILE=/etc/forwarder-agent/agent.env \
  server/deploy/osgallery-dev/install.sh
```

The units run this branch's checkout at `~/turku3d-mp/repo`. Do not switch that
checkout's branch while serving it. After updating committed code, reinstall or
run `systemctl --user restart turku-mp-server.service`; a restart clears the
unfinished hour. To stop this deployment:

```sh
systemctl --user disable --now turku-forwarder-agent.service turku-mp-server.service
```
