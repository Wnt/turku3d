# tools/dev

Static serving needs Node 20+. `environment.mjs` defines host-side paths and ports for Vite, Playwright, QA
and the dist server. Source the worker's `sandbox.env` before invoking any tool.

## Static server

`serve-dist.mjs` (handlers in `http.mjs`, shared with the localhost game's dev server) sends COOP/COEP, CORP and CORS headers, serves precompressed or on-demand brotli and gzip,
HTTP byte ranges, ETag/304 and MIME types for wasm, GLB, KTX2 and collision tiles. It defaults to
`$TURKU3D_WORK/dist` and port `$TURKU3D_PORT_BASE + 1` (main: 5171).

```bash
make game-preview       # build then serve game + tile mount
make game-serve-dist    # serve just the sandbox tile dist
# Explicit paths, same default port:
node tools/dev/serve-dist.mjs --root "$TURKU3D_WORK/build/game" --mount "/tiles=$TURKU3D_WORK/dist/tiles"
# Optional --port, --host, --no-compress, --candidates <dir>
make game-dev-tools-test
```

Tests use sandbox ports +3, +6, +7, +8 and +9, including simultaneous servers with separate output roots.

## Localhost game

`localhost-game` runs main hot on port base + 1 through the Vite dev server: HMR for code landings, an autosave
reload for the rest, tile releases from `$TURKU3D_DATA/tile-store` and a verification badge. The Vite dev server
(`make game-dev`) serves the same `/tiles`, `POST /telemetry` and status routes through `localhost/vitePlugin.mjs`.
See [localhost/README.md](localhost/README.md). The hot-path browser check runs its own dev server on port base + 4.

## Facade candidate viewer

`GET /facades/candidates/` on the localhost game (http://localhost:5171/facades/candidates/) and on
`serve-dist.mjs` shows every generated facade wall the reviewer saw, accepted or rejected, per content task:
`/facades/candidates/<task>/` lists the walls (rejected first) with their candidates side by side, the
reviewer's reasons, the retry feedback, the prompt summary and an "open in game" link (`?spawn=E,N,heading`,
8 m in front of the wall). `facadeCandidates.mjs` renders it from the JSON that
`textures/turkutex/facade_candidates.py` keeps under `$TURKU3D_DATA/facade-approvals/candidates/`
(`serve-dist.mjs --candidates <dir>` overrides it). `facadeCandidates.test.mjs` checks it on a synthetic task;
`make game-dev-tools-test` runs it with the serve-dist tests.

## Coarse tile service

`coarse-tiles` (localhost only, port `$TURKU3D_PORT_BASE + 5`) builds draft tiles for cells the
published tileset lacks (`pipeline/turku3d/coarse`). `serve-dist.mjs` and the Vite dev server proxy
`/coarse/*` to it, so the game stays same-origin; with the service down `/coarse/status` answers
200 `{"state":"offline"}` (other paths 503) and the game shows no drafts. `make game-dev` and `make game-preview` start it beside the server.
The localhost game service (`tools/dev/localhost-game`) runs it as a supervised child from main's worktree and
restarts it when a landing changes a file it loaded ([localhost/README.md](localhost/README.md)); run
`tools/dev/coarse-tiles` by hand only in a sandbox without that service, or with `localhost-game --no-coarse`.
`POST /coarse/drain` makes the service start no further builds (running ones finish), before a restart.

```bash
tools/dev/coarse-tiles                          # --port, --root (default $TURKU3D_WORK/coarse)
curl -s localhost:$((TURKU3D_PORT_BASE+1))/coarse/status
curl -s "localhost:$((TURKU3D_PORT_BASE+1))/coarse/t0_2_0/manifest.json?d=40"   # 202 queued, then 200
node tools/dev/serve-dist.mjs --root "$TURKU3D_WORK/build/game" --mount "/tiles=$TURKU3D_DATA/live/tiles"
```

`GET /coarse/<tile>/manifest.json?d=<m>` queues the tile and answers 202 `{"state": "queued",
"position": n, "eta": s}` (or `"building"` with its `eta`) until it is built; tiles not re-requested
within 10 s leave the queue. With a moving player (`/coarse/want`) the queue builds the tiles on the
path ahead first, by their distance along the heading, and the ring around the player after them;
standing, nearest first by the reported distance `d`. The ETA is the mean of the last eight builds
over the free workers. `/coarse/want` answers the build counter `version`; when it moves the game
polls its waiting tiles at once, so a finished draft arrives within a frame of the next check
instead of on the tile's own 2 s poll. The badge shows "ready in N s" and "next tile in N s".
One flat first-pass build and at most one full upgrade run at once, each on its own persistent worker process (`python -m turku3d.coarse worker`,
restarted when a pipeline source changes, after 50 tiles, or when it dies): the game also asks for the cells within the next
30 s of travel along the player's heading (`world/coarseTiles.ts`), so a cold tile's minutes of raw-data
download overlap with the warm builds. `/coarse/status` lists the tiles `building`. Built tiles are reused
until the producer code changes. First passes open the edge with structural collision before texture codecs, classified lidar surveys or decorative inputs run; full drafts replace them in place. Content-addressed payload URLs keep first-pass readers safe during upgrades. `status.upgrading` identifies the background full build.
A failed tile answers 404 with the reason and is retried after two minutes.
A build never
downloads: while a tile's raw inputs are missing it answers 202 `{"state": "pending-data",
"missing": [units]}` and the game shows "waiting for ... data". `GET /coarse/want?x=&z=&vx=&vz=`
(sent by the game once a second) steers the raw-data prefetcher; `/coarse/status` reports it under
`prefetch` (`pipeline/turku3d/coarse/README.md`).

```bash
curl -s "localhost:$((TURKU3D_PORT_BASE+5))/coarse/want?x=600&z=100&vx=27&vz=0"   # {"queued": n, "fetching": [...]}
curl -s localhost:$((TURKU3D_PORT_BASE+5))/coarse/status | python3 -m json.tool | grep -A30 '"prefetch"'
```
Playwright and QA profiles use `TURKU3D_BROWSER_DIR/tmp`. See [agent tooling](../../scripts/dev/README.md).

Build outputs are local; no sync step is needed. `make tile-show` serves the locally built tiles.

## Google aerial comparison

`make gmaps-compare` captures local-only Google/game reference pairs using the QA camera.
It opens visible sandbox Chrome and enables Globe/3D after navigation. Six locations were
captured, but altitude/FOV alignment and overlay removal remain unverified. See
[commands and limitations](../compare/README.md). Never publish its screenshots.
