# Draft world streaming

The draft service is the only world source in play and QA, on localhost and Pages.
The game starts with an empty runtime inventory (`draftManifest`) and requests the
nearest-first 80 m draft footprint around the spawn. It finds a nearby mapped street with actual worker-acknowledged collision and samples the car chassis and player footprints, rejecting roofs, walls and unsupported ground. It waits for actual spawn collision,
then `preloadStarts` retains the 80 m collision union of all selectable starts
while the loading screen uploads textures and warms the GPU. An offline service
keeps startup waiting and reports its state; there is no static tile fallback.

Mobile startup warms the initial camera view, keeping frustum culling enabled instead of
allocating GPU resources for every loaded mesh and panorama before showing the menu.
During mobile play, full textured models cover tiles within 80 m of the player (40 m
retention hysteresis). The existing predictive collision-surface reserve covers the farther
world; distant full models are released even if their tile remains in that reserve.
Desktop retains its complete 3 × 3 detailed window and panorama warmup.

`CoarseTiles` requests a complete visual/collision fragment and validates all sixteen
collision cells in the physics worker. Startup prepares the detailed GLB. During
movement, the exact cached collision triangles and solid primitives provide a
texture-free surface model first, so terrain and physics arrive together. Up to
two detailed GLBs load concurrently and replace those surfaces after styling and
GPU preparation. Existing facade results and textures remain on the detailed model.

Motion from physics drives prediction, including 288 m/s dev flight. The scan runs
at least four times per tile crossing. A bounded corridor prepares up to eight
future tiles with four concurrent surface preparations. Remaining slots maintain a
nearby surface reserve, covering sudden acceleration before a heading exists.
The obsolete single-candidate confirmation delay is removed.
`VisualTiles` owns resident models, shadow batches and model lifecycle events; it
performs no HTTP requests. `CollisionStreamer` loads the draft collision inventory
by distance and velocity, keeps edge walls at missing cells, and swaps changed cells. Entering play restores the normal 200 m ring and velocity lookahead.
The 80 m startup footprint covers the safe-street search; worker swept coverage still
prevents movement onto collision that has not arrived. There is no speed hold near the edge.

The local service receives position/velocity hints. Pages uses the public HTTPS
endpoint selected by `VITE_DRAFT_URL` (default `https://turku.lab.madekivi.fi/coarse/`),
polls tile requests directly, and sends no motion or private management requests.
Both clients recheck resident draft manifests every two seconds. Geometry,
collision and streets are replaced only after the changed payloads are ready;
collision-only updates do not require a visual change. Immutable payload URLs
allow the old scene to remain usable while a newer generator rebuilds it.

The public server serves complete cached pairs immediately and compares their generator
code hash, source identity and per-building stored facade identity in a bounded
background pool. Publication changes invalidate the delivery snapshot immediately. No automatic facade AI
calls run in the service. Existing stored facade results remain generator inputs.

```sh
source ../sandbox.env
tools/dev/coarse-tiles
# In a second shell, with the same sandbox environment:
make game-dev
cd game && npm run typecheck
npx vitest run tests/unit/coarse-tiles.test.ts tests/unit/coarse-region.test.ts
```

The former `?tiles=`, `?manifest=` and `?scene=dev` full-tile routes are removed.
QA uses the same draft service at its `?cam=` coordinates.

A failed draft under the player, car or another selectable start remains required.
Failures elsewhere may be skipped while their five-second retries continue. Payload
404s back off and retry; a failed worker replacement clears the main-thread resident
entry so coverage never claims colliders that the worker removed. Worker substeps check every swept footprint. The character and driven vehicle wait
only for cells with active collision requests, for at most 2.5 simulated seconds.
Unknown cells, deferred/failed requests and expired holds request immediate safe
recovery to the last safe sample, preserving the occupied car. Active request state
ends on success or failure; retry backoff does not extend a hold. Parked cars may
remain held. No main-thread speed slowdown is applied.

Manual public startup evidence (isolated Chrome, requires a browser slot):

```sh
python3 scripts/dev/host_slots.py browser -- node game/scripts/probe-draft-startup.mjs \
  https://wnt.github.io/turku-game/game/ "$TURKU3D_WORK/reports/draft-startup"
```

The probe checks Ready, no static tile requests, no runtime errors, player support
and drivable support under the own car, and retains a local screenshot and JSON.

OCI serving advertises optional `collision_bundle: {format: "sorted-col-v1", url,
sha256, bytes}` on each fragment. It is a content-addressed concatenation of that
fragment's sixteen unchanged `.col` files in lexicographic cell-id order. The
client checks the bundle hash/length, splits using each manifest byte length, and
validates every cell in the physics worker before exposing the visual. GLB decode
and road fetch overlap this validation. The streamer transfers retained validated
bytes to Rapier without a second HTTP request; consumed bytes are dropped and
unconsumed bytes are bounded by current visual residency. Older/local servers
without bundle metadata still use the same per-cell validation path.

Public hashed payloads and shared hashed assets are cached immutable for one year;
mutable manifests still revalidate. Caddy negotiates zstd/gzip for JSON, collision
and GLB responses. `Timing-Allow-Origin` exposes real OCI resource timings to the
Pages profiler. HTML preconnects to OCI and modulepreloads the large game entry
while the small bootstrap installs cross-origin isolation.

Headed maximum-speed regression (does not clear any cache or stop at missing edges):

```sh
python3 scripts/dev/host_slots.py browser -- node tools/perf/flight-loop/warm-suite.mjs RELEASE_LABEL
```

It flies actual dev input at 288 m/s, tests reversals and diagonal crossings, then
two simultaneous clients. Every frame checks visible terrain and worker collision
support. Each flight also waits for the detailed model to return after stopping.
Screenshots and timing evidence stay under the sandbox reports directory.
