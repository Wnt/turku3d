# Local development server

`serve-dist.mjs` is a dependency-free Node static server with COOP/COEP, CORP, CORS,
brotli, byte ranges, ETag/304 and MIME types for WASM/GLB/KTX2/COL.

```sh
make tile-show
# Explicit sandbox ports (example: w0-macpipe owns 5200..5209):
node tools/dev/serve-dist.mjs --root game/dist --port 5201 --mount /tiles="$TURKU3D_WORK/dist/tiles"
node --test tools/dev/serve-dist.test.mjs
```

Build outputs are local; no sync step is needed. `make tile-show` uses the sandbox port base + 1.
