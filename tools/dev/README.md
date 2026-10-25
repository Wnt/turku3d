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

Tests use sandbox ports +3, +4 and +5, including simultaneous servers with separate output roots.
Playwright and QA profiles use `TURKU3D_BROWSER_DIR/tmp`. See [agent tooling](../../scripts/dev/README.md).

Build outputs are local; no sync step is needed. `make tile-show` serves the locally built tiles.
