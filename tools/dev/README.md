# tools/dev/

Dev serving and syncing for the game and the tile dist. Neither tool has dependencies; they need Node ≥ 20, rsync and ssh.

## serve-dist.mjs

A static server with COOP/COEP (so `crossOriginIsolated` is true), CORP, CORS, brotli and range requests. Brotli uses a precompressed `<file>.br` if one exists; otherwise it compresses on the fly at quality 5 and keeps the result in an in-memory cache. Range requests (`206` / `416`) are served from the uncompressed file. The server also handles ETag/304 and sets the MIME types for `.wasm`, `.glb`, `.ktx2` and `.col`.

```bash
cd /Users/wnt/Documents/game-test
# the game build plus the mirrored tiles under /tiles  ->  http://127.0.0.1:8080/?tiles=/tiles/tileset.json
node tools/dev/serve-dist.mjs --root game/dist --port 8080 --mount /tiles=~/turku3d-data/dist/tiles
# just a dist dir
node tools/dev/serve-dist.mjs --root ~/turku3d-data/dist --port 8081
# options: --host 0.0.0.0, --no-compress
node --test tools/dev/serve-dist.test.mjs     # tests: headers, brotli, ranges, 404/traversal, wasm MIME, 304
```
Make targets: `make game-preview` (builds first), `make game-serve-dist` and `make game-dev-tools-test`.

## sync.sh

A one-way mirror from `osgallery-dev:~/turku3d/data/dist` to `~/turku3d-data/dist`. It never writes to the remote. `manifest.json` files are copied last. After syncing, every file listed in `tiles/manifest.json` is checked against its sha256.

```bash
tools/dev/sync.sh --dry-run
tools/dev/sync.sh                      # incremental
tools/dev/sync.sh --delete             # also remove local files that are gone remotely
tools/dev/sync.sh --only tiles/collision
REMOTE_HOST=osgallery-dev REMOTE_DIST=turku3d/data/dist LOCAL_DIST=~/turku3d-data/dist tools/dev/sync.sh
make game-sync ARGS=--dry-run
```
