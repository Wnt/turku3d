# tools/dev

Static serving needs Node 20+. `environment.mjs` defines host-side paths and ports for Vite, Playwright, QA
and the dist server. Source the worker's `sandbox.env` before invoking any tool.

## Static server

`serve-dist.mjs` sends COOP/COEP, CORP and CORS headers, serves precompressed or on-demand brotli and gzip,
HTTP byte ranges, ETag/304 and MIME types for wasm, GLB, KTX2 and collision tiles. It defaults to
`$TURKU3D_WORK/dist` and port `$TURKU3D_PORT_BASE + 1` (main: 5171).

```bash
make game-preview       # build then serve game + tile mount
make game-serve-dist    # serve just the sandbox tile dist
# Explicit paths, same default port:
node tools/dev/serve-dist.mjs --root "$TURKU3D_WORK/build/game" --mount "/tiles=$TURKU3D_WORK/dist/tiles"
# Optional --port, --host, --no-compress
make game-dev-tools-test
```

Tests use sandbox ports +3, +6, +7, +8 and +9, including simultaneous servers with separate output roots.

## Coarse tile service

`coarse-tiles` (localhost only, port `$TURKU3D_PORT_BASE + 5`) builds draft tiles for cells the
published tileset lacks (`pipeline/turku3d/coarse`). `serve-dist.mjs` and the Vite dev server proxy
`/coarse/*` to it, so the game stays same-origin; with the service down `/coarse/status` answers
200 `{"state":"offline"}` (other paths 503) and the game shows no drafts. `make game-dev` and `make game-preview` start it beside the server.

```bash
tools/dev/coarse-tiles                          # --port, --root (default $TURKU3D_WORK/coarse)
curl -s localhost:$((TURKU3D_PORT_BASE+1))/coarse/status
curl -s "localhost:$((TURKU3D_PORT_BASE+1))/coarse/t0_2_0/manifest.json?d=40"   # 202 queued, then 200
node tools/dev/serve-dist.mjs --root "$TURKU3D_WORK/build/game" --mount "/tiles=$TURKU3D_DATA/live/tiles"
```

`GET /coarse/<tile>/manifest.json?d=<m>` queues the tile (nearest to the player first; the latest
distance wins; tiles not re-requested within 10 s leave the queue) and answers 202 until it is built.
One build runs at a time in a child process. Built tiles are reused until the producer code changes.
A failed tile answers 404 with the reason and is retried after two minutes.
Playwright and QA profiles use `TURKU3D_BROWSER_DIR/tmp`. See [agent tooling](../../scripts/dev/README.md).

Build outputs are local; no sync step is needed. `make tile-show` serves the locally built tiles.
