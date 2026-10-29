# Draft world streaming

The draft service is the only world source in play and QA, on localhost and Pages.
The game starts with an empty runtime inventory (`draftManifest`) and requests the
nearest-first 80 m draft footprint around the spawn. It finds a nearby mapped street with actual worker-acknowledged collision and samples the car chassis and player footprints, rejecting roofs, walls and unsupported ground. It waits for actual spawn collision,
then `preloadStarts` retains the 80 m collision union of all selectable starts
while the loading screen uploads textures and warms the GPU. An offline service
keeps startup waiting and reports its state; there is no static tile fallback.

`CoarseTiles` requests `/coarse/<tile>/manifest.json`, validates all sixteen collision
cells in the physics worker, styles the GLB offscreen, and only then exposes the model.
`VisualTiles` owns resident models, shadow batches and model lifecycle events; it
performs no HTTP requests. `CollisionStreamer` loads the draft collision inventory
by distance and velocity, keeps edge walls at missing cells, and swaps changed cells. Entering play restores the normal 200 m ring and velocity lookahead.
The 80 m startup footprint covers the safe-street search; worker swept coverage still
prevents movement onto collision that has not arrived. There is no speed hold near the edge.

The local service receives position/velocity hints. Pages uses the public HTTPS
endpoint selected by `VITE_DRAFT_URL` (default `https://turku.lab.madekivi.fi/coarse/`),
polls tile requests directly, and sends no motion or private management requests.
Both clients recheck resident draft manifests every fifteen seconds. Geometry,
collision and streets are replaced only after the changed payloads are ready;
collision-only updates do not require a visual change. Immutable payload URLs
allow the old scene to remain usable while a newer generator rebuilds it.

The server compares each cached manifest with the current generator code hash,
source identity and per-building stored facade identity. No automatic facade AI
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
entry so coverage never claims colliders that the worker removed. Worker substeps
hold both the character and every car when the swept footprint lacks collision.

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
