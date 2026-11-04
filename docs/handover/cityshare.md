# City share: the public next-gen test at https://city.lab.madekivi.fi/

A public share of the local next-gen game (integration branch `local/loop-integration`) for friends to play.
It runs entirely on osgallery-dev as systemd user units (linger), so the Mac can be off. The coordinator's
browser check in Chrome on 2026-11-04 passed: WebGPU, cross-origin isolation, 920 tile responses, multiplayer
connected, no console errors.

## Update the share

From the Mac:

```sh
~/turku3d/wt/loop-integration/repo/tools/share/deploy.sh [REV]   # default: local/loop-integration
```

The script:

1. Builds REV in a detached worktree with `VITE_SHARE_HOSTS=city.lab.madekivi.fi` and `VITE_PUBLIC_RELEASE=1`
   (the Pages start menu, Multiplayer only).
2. Uploads the dist and the server sources to `~/turku3d-local/city/releases/<sha>` on osgallery-dev and keeps
   the last 3 releases.
3. Re-pins the tile proxy to the local game's tile version (`~/turku3d/loops/integrator/tile-service.json` on
   the Mac). Rerun the script after a tile repin; it works for the same commit too.
4. Switches `current`, installs changed units, restarts `city-web` and `city-tiles`, and restarts `city-mp` only
   when `server/` changed.
5. Checks that `https://city.lab.madekivi.fi/version.json` names the new commit.

Tiles resolve per tile, newest generation first: `tiles.json` carries `follow: "train"` (deploy.sh,
`TURKU3D_SHARE_FOLLOW`/`TURKU3D_SHARE_EXCLUDE`), and city-tiles serves a tile's fragment from the highest farm alias
`train<N>` whose `ready.json` for that tile exists and names the pin's base generation, else from the pin. A new train
joins by registering its alias; a train on a structurally different generation is never mixed in (repin instead), and
`exclude` drops a train Jonni has not accepted. Trains are only read, never asked to migrate; demand and status go to
the pin. Resident tiles switch within the client's ~30 s refresh. `/version.json` lists `follow` and `exclude`.

A web restart drops multiplayer sockets for about a second; the client reconnects with its session.

## Client behaviour

`game/src/core/localOnly.ts` `isShareHost`: a `VITE_SHARE_HOSTS` hostname is served like a local origin, but
it is public like Pages. Unit tests: `game/tests/unit/share-host.test.ts`.

- Same-origin `/coarse/` tiles, `./pointcloud/` and `wss://<host>/` multiplayer. The published server is never
  used.
- None of the local-only features run there (telemetry, capture, dev HUD); `?dev=1` still opens the dev menu.
  `?editor=1` runs the object editor with edits kept in the browser only.

## Units, ports and hardening (osgallery-dev, `systemctl --user`)

All units are enabled for boot with `Restart=always`. The city units' sources are in `tools/share/units/`.

| Unit | Listens on | Role |
|---|---|---|
| `city-web` | 127.0.0.1:7780 | `tools/dev/serve-dist.mjs --public --mp`: the game, `/pointcloud` mounted from `/bulk/turku3d/lidarpc/copc`, `/coarse` via city-tiles, the WebSocket plus `/live.json` and `/scores/*` via city-mp |
| `city-tiles` | 127.0.0.1:7781 | `scripts/dev/shared-tile-proxy.mjs`: to the farm's `/v/<pin>` and the scenery server; pin in `~/turku3d-local/city/tiles.json` (hot-read) |
| `city-mp` | 127.0.0.1:7782 | `server/index.js`: its own room and scores (`~/turku3d-local/city/mp-scores`); `TURKU_MP_ORIGINS=https://city.lab.madekivi.fi`; no producer pings |
| `city-forwarder` | outbound only | `forwarder-agent --tunnel city:http:city.lab.madekivi.fi:7780`; the token is the existing `~/.config/turku3d/forwarder.env` |
| `shared-tiles` | 192.168.1.114:7950 | the shared tile farm, 4 workers; also used by the local game and agents |
| `local-horizon` | 127.0.0.1:7947 | the scenery server (checkout `~/turku3d-local/loops/gen2arch/scenery-server/<sha>/repo` named in the unit's `WorkingDirectory`, `PYTHONPATH=…/pipeline`); `/coarse/scenery/health` reports inputs, cache and last served |
| `lidar-copc` | 0.0.0.0:7960 | the COPC server for the LAN |

The last three units replaced the hand-started processes with the same commands. Their logs still go to
`~/turku3d-local/loops/coordinator-restart/*.log`.

Public hardening in `serve-dist --public`:

- GET and HEAD only: POST, PUT and OPTIONS get 405, so there are no capture, telemetry or other uploads.
  - Edit sessions are never uploaded either. With `?editor=1` the editor runs in local-only mode, and its close
    dialog hands over the session JSON (see `editor.md`).
- No facade candidates, `/__localhost`, `/healthz`, dotfiles or directory listings.
- `/coarse` takes plain path segments only (dot segments and percent escapes get 404), so nothing reaches the
  farm's root status or management routes.
- Tile status routes (`health`, `status`, `demand`, `want`) answer only `{state, version}`.
- Tile payloads are brotli-compressed for the tunnel, about 3x smaller. Encoded immutable (revision-named) payloads
  stay in a 1 GiB memory cache (`--payload-cache-mb`), so a repeat skips the farm read and another brotli pass.
- `city-tiles` keeps a finished manifest/collision fragment 15 s per tile (3 s while its tile is being rebuilt) for
  every player and distance, joins identical concurrent requests and answers polls with an ETag/304. A player's own
  tile (`d < 1`) still reaches the farm, which tracks where players stand. It resolves a tile's train chain with one
  `GET /receipts?tile=…&v=…` farm request.

## Deploy the tile farm from integration

The farm daemon (`shared-tiles`) runs `~/turku3d-local/loops/tiles/shared_tiles.py`, a copy of
`scripts/dev/shared_tiles.py`; `DEPLOYED_FROM` next to it names the integration commit. The copy must not lag
integration: the city passes rely on its spot rebuilds. Redeploy after every merge that changes
`scripts/dev/shared_tiles.py` or `scripts/dev/shared_sources.py`. The other files the farm uses
(`shared-tiles-run.sh`, generator code) come from each registered revision's snapshot, not from this directory.

```sh
cd ~/turku3d/wt/loop-integration/repo
H=$(git rev-parse --short HEAD)
scp scripts/dev/shared_tiles.py scripts/dev/shared_sources.py osgallery-dev:/tmp/
ssh osgallery-dev "cd ~/turku3d-local/loops/tiles && python3 -m py_compile /tmp/shared_tiles.py &&
  for f in shared_tiles.py shared_sources.py; do cp \$f \$f.bak-\$(date +%Y%m%d-%H%M); cp /tmp/\$f \$f; done &&
  echo $H > DEPLOYED_FROM && systemctl --user restart shared-tiles"
```

- The restart takes about 2 s. The farm's builds run in its unit's control group, so systemd stops them too: they
  are logged as `failed` ("retry will resume cached stages") and run again on the next request; a city pass asks
  again by itself. `shared-tiles-upgrade.py` (adopting running builds) predates the unit and does not apply to it.
- The city passes (`tile_city_pass.py`) run in their own sessions and survive the restart; they treat a busy or
  missing farm as "ask again".
- Check: `/health` answers; a built tile's `manifest.json` per generation level (base, pin, each `train<N>`) is
  byte-identical to before the restart (only a tile whose queue state moved may differ in `queue_state`); a spot
  rebuild request (`GET /v/<rebuild_revision>/coarse/<tile>/manifest.json`, no `d`) shows up in `/status` `active`.

## Forwarder limit and bandwidth

- `city.lab.madekivi.fi` has no host policy on the forwarder, so the default of 256 concurrent requests per
  agent applies.
  - That is about 7 players at 32 parallel tile fetches each. Beyond that, requests get 503 with
    `Retry-After: 5` and the game retries.
  - `turku.lab.madekivi.fi` has 1,024 through `~/forwarder/deploy/hosts.json`.
- Measured throughput (2026-11-05): osgallery-dev uploads about 67 MB/s (530 Mbit/s) to the internet; a round trip
  osgallery-dev → forwarder → osgallery-dev carries 45 MB/s on one stream, 48 MB/s on four. The earlier 10 MB/s
  figure was a Mac on Wi-Fi, which reaches the LAN box at about 19 MB/s itself.
- A long drive is about 0.13 GB compressed (0.4 GB raw). A first load is at most about 52 MB of compressible
  assets; voice clips load on demand.

## Health check (curl only)

```sh
curl -s https://city.lab.madekivi.fi/version.json        # deployed sha, subject, tile pin
curl -s https://city.lab.madekivi.fi/coarse/health       # {"state":"ready","version":…}
curl -s https://city.lab.madekivi.fi/coarse/scenery/health  # local-horizon: inputs, cache, last served; 503 if degraded
curl -s -o /dev/null -w '%{http_code}\n' 'https://city.lab.madekivi.fi/coarse/t0_0_0/manifest.json?d=0'
curl -s https://city.lab.madekivi.fi/live.json | head -c 200
ssh osgallery-dev 'systemctl --user is-active city-web city-tiles city-mp city-forwarder shared-tiles local-horizon lidar-copc'
```

The user journal is not readable on osgallery-dev. `systemctl --user status <unit>` shows the last lines.

## Open items (ranked)

1. **More players:** add `city.lab.madekivi.fi` to `~/forwarder/deploy/hosts.json` (for example
   `max_conns: 1024`, like turku) and deploy the forwarder through its CI.
2. **The hand-started loop-tile-server** on osgallery-dev `192.168.1.114:5225` (`loop-tile-server.mjs`) has no
   unit and will not survive a reboot. Its command is in
   `~/turku3d/loops/coordinator/osgallery-services-before-break.txt`, which should also point at the three new
   units.
3. **osgallery-dev disk is at 95 %** (about 35 GB free; the farm's floor is 25 GB). The share uses about
   130 MB.
4. The farm sometimes holds its lock during cache eviction; a `/coarse/demand` call once took over 8 s.
   Clients back off and retry.
