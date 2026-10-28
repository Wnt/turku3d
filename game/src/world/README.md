# game/src/world

Streaming of the published tiles (`docs/specs/tiles.md`): `manifest.ts` loads and checks `manifest.json`, `tiles.ts`
streams the visual tileset with 3DTilesRendererJS. Play uses `loadRegion.ts`: a 250 m full-detail ring around the
player (or the collision radius when larger), independent of camera orientation. Every intersecting L0 tile and
its REPLACE siblings load; a parent stays refined for another 125 m before eviction. Far L1 ground within 2 km
also loads in every direction. Mesh frustum culling still limits actual rendering. QA retains the nine-L0
camera/SSE budget in `residency.ts`. `collisionStreaming.ts` keeps `.col` tiles within the collision radius in the physics worker,
`shadowBatch.ts` merges non-vegetation shadow casters on layer 1, enabled only on sun shadow cameras
(including the cloned CSM cascades). Colour, normal and reflection cameras retain layer 0, so the
shadow proxies never submit duplicate geometry to those passes. `coarseTiles.ts` streams localhost draft tiles for cells the
tileset lacks (`tools/dev/coarse-tiles`).
The draft badge's building count comes from `/coarse/want` (`queued` plus the running `building` ids), so retained
client entries outside the requested area do not count as work. Verify locally with `scripts/dev/gate --fast --base main`.

## Startup preload and progress

`preload.ts` runs after actor and parked-car assets load and before the session exposes `__game.ready` or enables
the start menu. It pumps the ordinary tile, draft and collision streamers until every required visual model,
draft in the existing 3×3 window, ground binding and worker-decoded collision cell has arrived. Required failures
are surfaced, rather than treating an idle queue as success. The tileset is indexed before traversal, so neither
an empty first traversal nor `loadProgress === 1` alone can release the screen.

Textures in PBR slots and TSL graphs are uploaded with `initTexture`; `compileAsync` sees offscreen meshes too.
Six real warmup views exercise shadow/MRT/post-processing and vegetation variants on WebGPU and WebGL2, followed
by the restored initial view. During play the same distance-region plugin feeds the library's own priority queues,
parsers and LRU cache. Used ring content cannot be evicted. Cache admission reserves 256 MiB above current protected
content: the library's fixed default byte cap could otherwise stop refinement with required tiles still missing.
Unused content still evicts and the allowance shrinks as the player leaves an area.

The loading UI can poll `worldLoad.snapshot()` (`loadProgress.ts`) or `window.__game.loadProgress()`. Snapshots contain
`phase` (`loading`, `compiling`, `ready`, `error`), `elapsedMs`, `bytesLoaded`, `visual: {loaded,total}`,
`textures: {loaded,pending}`, `collision: {loaded,total}`, `compile: {done,total}` and `errors`. Counts represent
required models/cells and actual upload/warmup work. Bytes are completed encoded HTTP response bodies, not an
estimated total or partial in-flight transfer count. Compile progress counts six rendered directions.

Manual cold-cache proof (outside the gate; uses fresh sandbox Chrome, 10 Mbps/40 ms networking and 4× CPU throttle):

```sh
source ../sandbox.env
# Serve a sandbox copy of a fixed real tile release beside a production game build.
scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py build -- npm --prefix game run build
node tools/dev/serve-dist.mjs --root "$TURKU3D_WORK/build/game" --port "$((TURKU3D_PORT_BASE + 1))" \
  --mount "/tiles=$TURKU3D_WORK/dist/tiles"
# In another shell with the same sandbox environment:
scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py browser -- \
  node game/scripts/preload-probe.mjs after webgpu
# Repeat with webgl. Reports/screenshots: $TURKU3D_WORK/reports/preload/<label>-<backend>/.
```

## Collision edge and draft tiles

Ground is drawn farther than collision reaches: the published far LOD (500 m and 1 km parents) covers the city around
the playable tiles, and a draft tile can be on screen before its cells stream into the physics worker. Two rules keep
the player from falling through visible ground:

1. **Edge walls** (`physics/edgeWalls.ts`): wherever a loaded `.col` tile borders a cell with no collision in any
   source (outside the published tiles and every drawn draft, a draft still building or rejected, a tile whose load
   failed for good), the worker closes that edge with an invisible box (2 m thick, 20 m below to 40 m above the
   tile). The character and every vehicle stop there; rays and the camera cast pass through. `CollisionStreamer`
   tells the worker which neighbours of the loaded tiles exist (`knownTiles`: in the manifest, drafts included, not
   failed for good); no wall faces those. Until it does, an edge is closed. At the real edge of the built area the
   HUD edge notice and the minimap's red edge (`gameplay/recovery.ts`) explain the stop.
2. **Collision before visibility** (`coarseTiles.ts`): a draft is added to the scene only when every one of its 16
   collision cells has an entry (published entries win) and the worker has decoded each draft `.col`
   (`PhysicsClient.checkTile`, no colliders are built). A draft that fails either check is never drawn; the HUD names
   the reason (`draft tile t0_i_j unavailable: ...`) and it is asked for again after 120 s.
3. **Collision follows what is drawn**: a drawn draft's `.col` files serve all 16 of its cells, also the published
   margin cells there (the ring of collision the release ships around the playable tiles: ground, and in releases
   built before the per-tile props producer, props whose visuals sit in an unpublished tile). The replaced entries
   come back when the draft leaves (`drop`), and loaded colliders are swapped in place both ways
   (`CollisionStreamer.refresh`). A release swap leaves cells a draft holds to the draft (`CoarseTiles.held`) and
   hands it the release's entries to return (`CoarseTiles.release`).

**The visible edge**: the player sees where the drivable world ends before reaching it.

- `edgeMarker.ts` draws a low translucent band with amber and dark diagonal stripes (1.1 m above the ground, opaque
  at the bottom, a bright line on top) along every true edge (`CollisionStreamer.trueEdges`, the sides the worker
  walls off). It follows the ground, sampled by 26 downward rays per 62.5 m edge just inside the loaded tile, and
  leaves when the edge does (a neighbour's collision arrived). The play session owns it (`__game.edgeMarker`).
- `render/unbuilt.ts` draws far LOD (500 m and 1 km tiles) flat grey with faint stripes wherever no full-detail
  tile is: no published L0 tile and no drawn draft (`CoarseTiles.drawn`). Ground drawn only by the far LOD has at
  most the published margin ring of collision and never buildings or props, so it reads as unbuilt; it turns
  coloured once a draft is drawn there. A 250 m mask texture over the L0 grid drives it; QA renders are unchanged.
- The HUD edge notice and the minimap's red outline of the collision area (`gameplay/recovery.ts`) mark the same
  edge.

**Prefetch**: besides the 3x3 window it loads, `CoarseTiles` asks the service to build every cell within the next 30 s
of travel along the player's heading plus a one-tile ring (`aheadTiles`; speed from the positions of its 1 s checks,
a jump above 100 m/s is a teleport). Those tiles are only built (state `built`), not loaded, until they enter the
window. The service builds four at once, so a car at full speed meets built tiles once their raw data is on disk.
The HUD badge names the tile underfoot, lists the drafts loaded and counts those still building. A draft's street
graph (`roads.json`) is fetched with it; `roadGraphs()` returns those of the drafts in the scene and `onMapChange`
tells the map to re-merge (`gameplay/README.md`, "Draft tiles' streets").

Draft entries then join `manifest.collision`, so `CollisionStreamer` loads them with the normal radius, the walls move
out with them and `SafeReset`'s playable area grows.

**Collision ahead**: `CollisionStreamer.update` takes the player's ground velocity and also loads the tiles along the
next 8 s of travel (one-tile corridor, above 3 m/s), ranked nearest first with a bonus for lying ahead, and keeps them
while they stay ahead. A car at full speed (40 m/s) thus meets loaded collision 520 m out with the default 200 m
radius. If it still reaches a tile that exists but is not loaded (a slow network, a retry), `speedLimit` gives the
speed that stops 1.5 m before it at 6 m/s², and `play.ts` brakes the car (`holdCar`) or slows the walk (`holdFoot`)
to it: a gentle hold, not a wall.

## Tile releases swapped in place (`releaseSwap.ts`)

On the localhost game (`tools/dev/localhost/README.md`) the dev server's `/tiles` moves to a new tile release when a
landing or a builder changes tiles (the integrator's unverified tile build, or per-tile entries from `make tile-add`
and the service's quick builds composed over it). The service pushes the release key in the `turku3d:status` event; `main.ts` then calls `swapRelease`:

1. The new `manifest.json` is fetched (`cache: no-store`) and compared with the loaded one by `sha256`
   (`releaseDelta`): visual tiles added or changed, collision tiles added, changed and removed, and the tileset.
2. The loaded manifest object is updated in place, because `CollisionStreamer`, `SafeReset`'s playable area and
   `CoarseTiles` hold it. Removed collision tiles unload; changed ones that are loaded are fetched again and replace
   their colliders in a single worker message (`CollisionStreamer.refresh`), so the player never falls through;
   added ones load when the player comes near. Draft entries (absolute `/coarse/` URLs) are kept.
3. When any visual tile or the tileset changed, `VisualTiles.reload` loads the new tileset hidden beside the current
   one and swaps them once it has settled for the current view. `tileReuse.ts` compares visual manifest hashes and
   transfers unchanged loaded model objects, geometry, materials and shadow batches at the swap; only changed or
   newly visible models are fetched and styled. The old renderer relinquishes transferred resources before disposal; listeners subscribe to `VisualTiles.events`, which
   carries the models of both, so material styling, ground textures, telemetry and draft-tile parents follow.
   `VisualTiles.swaps` counts swaps. A playable-tile list change arrives the same way: the new tileset has the new
   tiles, and `CoarseTiles.release` retires drafts of tiles it now renders.
4. If the swap fails, the page autosaves and reloads into the new release.

The dev server serves `/tiles` with `Cache-Control: no-cache`, so every fetch revalidates by ETag and a swap never mixes
cached bytes of the previous release. Production builds have no swap path (`import.meta.hot` is undefined).

```bash
cd game && npx vitest run tests/unit/release-swap.test.ts tests/unit/streaming.test.ts tests/unit/coarse-tiles.test.ts \
  tests/unit/edge-walls.test.ts tests/unit/edge-marker.test.ts
python3 ../scripts/dev/host_slots.py browser -- node_modules/.bin/playwright test -c playwright.hotswap.config.ts
# Full throttle to the true edge on the dev tiles with a fake draft; screenshot in $TURKU3D_WORK/reports/fix-edge-walls-streaming/
npm run build && python3 ../scripts/dev/host_slots.py browser -- node_modules/.bin/playwright test edge-walls.spec.ts coarse-drafts.spec.ts
```

## Vegetation LOD

`vegetationLod.ts` handles both published and draft `EXT_mesh_gpu_instancing` trees
(including older tiles): 50 m cells cull against the camera frustum and distance,
then compact visible matrices into at most three draws per tile/prototype.
The draft tint stays in ordinary PBR colours on untextured trees.
Compatible opaque, untextured bark/crown parts with identical placements share a
vertex-coloured mesh; differing PBR settings, textures and node materials retain
separate draws. Clustering preserves the part colours, and far silhouettes retain
the crown colour. Crowns already cheaper than 32 triangles share their mid/far draw.
Near trees retain the source mesh and cast shadows; mid trees use vertex-clustered
prototypes; far foliage uses 32-triangle opaque crossed silhouettes sampled from
the same prototype. Bark disappears at far distance. No imagery or alpha testing
is needed. `render/quality.ts` bounds near/mid/far submitted instances globally
per frame (material parts each count); overflow uses a cheaper LOD, then culls.
Stationary cell selections reuse their matrix buffers. Tile disposal releases the
derived meshes, and release reuse retains them. Draft registration runs after tint
through `CoarseHost.styled`. Independent trunk collisions are unchanged. Verify
with `scripts/dev/gate --fast --base main`; pure geometry/culling tests are in
`game/tests/unit/vegetation-lod.test.ts`. The producer adds only constant metadata
per prototype, with no added tile-build stage; build-time overhead was not timed.

### Manual Kupittaa render measurement

The fixed hotspot is engine `(1375,19,625)`, looking at `(1420,18,450)`, 65° FOV,
medium quality at 2560×1440. Snapshot the nine draft tiles `t0_4_1` through
`t0_6_3` **and their `shared/` textures** into a sandbox directory. Keep these
bytes fixed while comparing versions; the report records each GLB hash.

```sh
source ../sandbox.env
# Terminal 1: sandbox Vite server
cd game && npm run dev -- --host 127.0.0.1
# Terminal 2, from the repository root
scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py browser -- \
  node qa/render/kupittaa.mjs --drafts "$TURKU3D_WORK/reports/kupittaa/drafts" --label kupittaa
```

The manual probe applies the draft tint and alternates pre-LOD (`382fb21c^^` shadow batching), landed LOD
(`382fb21c`) and the working tree in one Chrome/Metal process, three repetitions,
with and without shadows. It uses the shared renderer, sky, post-processing and GPU
timer, warms 120 frames and measures 240 with unlocked vsync and a final GPU drain.
It creates temporary probe files and removes them on exit. No browser spec or gate
budget is added. `--modes fixed` measures a changed density once; `--url`, `--out`
and `--label` select the sandbox server and report paths. Reports include errors,
all-pass calls/triangles, CPU/GPU/work percentiles and drained ms/frame. Compare
work time and drained throughput; a vsync-limited RAF interval conceals headroom.
See [Kupittaa measurements](../../../docs/reports/perf-kupittaa-check.md).
