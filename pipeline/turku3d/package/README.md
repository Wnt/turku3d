# Layer assembly and packaging

Production consumes the complete manifests under `$TURKU3D_WORK/build/layers/`.
Publish layers first with [`turku3d.layers`](../../../docs/specs/layers.md).
The office build requires every layer `make refresh` rebuilds (`OFFICE_LAYERS` in
`assemble.py`: ground, buildings, an acceptance-passed structures layer, bridges,
surrounding building massing, props, vegetation, traffic_static, roads and landmarks),
each with the visual/collision tiles it publishes. A layer whose manifest lists
`params.replaces` (landmarks) replaces those buildings-layer footprints: the assembler drops
their collision triangles (by `building_index`) and visual nodes (by `building_id`). A missing or empty layer fails the build and
names `make refresh`; `pipeline/tests/test_refresh.py` keeps the two lists equal.
A requested tile needs at least one visual layer.
L0 and collision have no stub or raw-data fallback. Coarse parents may read the cached
maanpinta ground points and 2022 ortho to fill their complete footprint.

```sh
make refresh
make tile-build T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
make tile T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
make tile-test
bash pipeline/env/run.sh python -m turku3d.package.facade_ground_audit
make game-test
```

`make refresh` locks `$TURKU3D_WORK/.refresh.lock`, checks producer manifest input and
code hashes, and rebuilds changed ground and buildings in parallel, then structures.
Structures use the refreshed ground and must pass real-data geometry and Rapier
acceptance. Ground splats, the filtered facade export, bridges, roads, landmarks and surrounding
building massing follow in parallel. The instance producers then run in placement
order (props, vegetation, traffic_static) against all of those colliders; each one
clears only the inventories published before it, so one pass converges.
The tile build also publishes the delivery address pool beside the road graph
(`manifest.addresses`, [`turku3d.content`](../content/README.md)).
Ground splats reuse
the fused orthos when the ground layer changes. A second run with unchanged inputs
prints `cached` for every present product. It reads shared raw inputs and writes
only derived products under `$TURKU3D_WORK`. Run it before `make tile-build` after
changing a layer. The tile builder rejects stale ground bindings and names this
refresh target instead of shipping flat ground. A changed QA-approved facade export
needs a new filtered export; refresh reports that case explicitly because the
original QA inputs are required to regenerate it. If buildings code or inputs are
stale while an approved facade export exists, refresh stops before replacing the
building layer: changed wall identities need a new facade QA pass. Use
`make refresh REFRESH_TARGET=ground-textures` to refresh ground and splats while
that facade work is pending.

`make tile` assembles, validates with Rapier, then runs Chrome WebGPU/WebGL2 tests.
Output is `$TURKU3D_WORK/dist/tiles`; reports are `$TURKU3D_WORK/reports`.
The test browser uses `$TURKU3D_BROWSER_DIR`, on the sandbox port base + 1.
To use an explicit layer inventory or texture library:

```sh
bash pipeline/env/run.sh python -m turku3d.package.assemble \
  --layers "$TURKU3D_WORK/build/layers" --out "$TURKU3D_WORK/dist/tiles" \
  --library "$TURKU3D_WORK/textures/assembly/library" \
  --facades "$TURKU3D_WORK/textures/assembly/facades" \
  --tile t0_0_0 t0_0_1 t0_1_0 t0_1_1
```

Collision merges preserve all triangles, per-face material IDs, and primitives. Positions
are converted from float64 world-local into tile-local float32; every output is decoded,
checked against the source within 2 mm, and limited to 1 MB. Continuous-layer seams are
checked bitwise before and after conversion, including neighbouring previously shipped tiles.
Complete objects are not clipped or duplicated. `AOI_BORDER` follows the available collision
inventory, and changes on existing neighbours when new tiles are added.
Available collision tiles in the 50 m margin around each requested L0 tile are also
published. This includes the west Teatterisilta shore outside the four visual tiles.

Visuals preserve L0 topology and source object IDs. Compatible primitives are batched per tile, layer and material.
Library-backed materials use the selected library key as their material identity, so
different facade or roof classes that resolve to the same library maps form one batch
per layer and library material. The number of these batches does not grow with the
number of buildings.
Batches carry `extras.objects` with source metadata and index `start`/`count` ranges; the QA ID
pass expands these ranges into draw groups only while capturing IDs. Normal rendering uses one
draw per batch. Texture and material deduplication prevents repeated glTF texture transforms
from exhausting the streaming cache before L0 can load. The assembler binds library albedo/normal/ORM
maps and unique building facades, falling back to the library material when a facade is absent.
An approved facade sidecar may request `min_pixels_per_metre` (4–72). The
7.6 MB headroom planner first reduces other facades, then reduces that map only as far as
its declared minimum. If the tile cannot meet the fixed 8 MB wire cap, assembly fails.
Both texture defaults are under `$TURKU3D_WORK/textures/assembly`. The assembler prints
`facades_bound` in its JSON result, including on a cache hit. For assets exported to a
different work root, pass its `library` and `facades` paths explicitly.
Only W1-09's filtered export may supply unique facades: its parent `MANIFEST.json` must
have `bound` facade IDs (`<building_id>_<facade_index>`) and an `inventory_sha256` recording its
source inventory. Each bound facade must still appear in the current `layers/buildings/facades.geojson`
with matching width and height within 1 cm; unrelated inventory changes do not disable its map.
Missing or changed facade geometry keeps the library fallback.
Raw staging is rejected. Unlisted indices are ignored even when matching files exist.
`facades.py` holds the binding, per-tile facade inputs and density planning. A visual tile's
cache key covers only the bound IDs and exported files of its own buildings (those its layer
GLBs carry, plus those whose facades are centred in it), so facade or building changes in
another tile leave it packed.
Sidecars must identify the building/index, declare metre UVs, pass `qa.gltf_basisu_valid`,
and supply all three `files` and matching SHA-256 `hashes`. Changed exported maps fail assembly.
Facade maps have a package-time texel density cap: 48 pixels per metre for full-height
walls and 72 pixels per metre for short, ground-floor walls, never exceeding the source
dimensions. When a tile reaches the 7.6 MB headroom target including ground maps, the
assembler steps facades down the density ladder 16, 8, 4, 2, 1 pixels per metre, one
level at a time: always the facade with the highest planned density, the farthest from
the office spawn among equals, so every far facade drops to 16 before any drops to 8.
A facade stops at its declared minimum density. The planner estimates the savings from
each facade's exported map bytes and source density for a first batch, then extends by
a tenth of the tile's facades while measured wire bytes still exceed the target. Only
when every facade is at its minimum (or 1 pixel per metre) does it step below minimums. The fixed 8 MB wire limit remains enforced. The source files and QA hashes remain intact;
encoded variants are cached outside the export. The manifest records each tile's wire
size before and after adaptation as `budget_before_bytes` and `wire_bytes`.
For L0 tiles with multiple unique facade maps, the packager resizes maps directly
from their source KTX2 files and packs them into per-tile 2048-pixel atlas pages.
The atlas preserves glTF's top-left image coordinate: a source facade's lower
photo edge (v=1) stays at the lower edge of its atlas region. `make tile-test`
checks a two-metre door on synthetic walls placed in separate atlas rows.
`bash pipeline/env/run.sh python -m turku3d.package.facade_ground_audit`
checks every approved facade against ground collision triangles at its street side.
It blocks photo UV errors, missing geometry and walls floating more than 0.3 m
above surveyed ground. Bases more than 0.3 m below adjacent sloped ground are
reported individually as buried lower-wall sections, with the maximum burial.
Each page has aligned albedo, normal and ORM rectangles, a four-pixel copied-edge
gutter, and one shared material. The UASTC normal page is encoded at half the
albedo resolution with the same normalized UVs; albedo and ORM keep full resolution.
It bakes `uv.scale`/`uv.offset` with clamped
sampling into mesh UVs before geometry batching. `extras.objects` retains each
facade's ID and index range for the QA pass. Single-map tiles retain the
individual `KHR_texture_transform` binding. Library `material.json` `uv_scale` applies
to all library maps with repeating sampling; its default is `[1,1]`. Embedded source maps
retain their own UVs and transforms when no replacement map exists.

The single fallback table is `materials.mjs`: W1-03's four `facade_*` classes use
`render_smooth`; its four `roof_*` classes use `roof_standing_seam_metal`.
`plinth_granite`, `ground_asphalt`, and `ground_granite` use their namesake library assets.
Brick, wood and roof tile remain explicit generic approximations. Original `material_key`
is retained in node extras and `extras.objects` QA ranges. Library-backed material extras
use the chosen library key for both `material_key` and `library_material_key`.
Untextured inputs retain their source colors. PNG/JPEG maps become ETC1S albedo or UASTC +
Zstandard linear maps through KTX-Software 4.4.2 **before** lossless meshopt compression.
Facade albedo and ORM maps use ETC1S; normal maps use UASTC with Zstandard, retaining
normal detail. Library KTX2 maps remain UASTC where authored,
but are published once under `visual/shared/` and referenced by relative URI from each GLB.
The codec selection was measured on exported facade `103277350D_45` at 176 × 772 pixels:
ETC1S gave 42.64 dB albedo and 45.27 dB ORM at 12.6/11.1 KB, while UASTC used
44.0/31.3 KB; normals measured 29.09 dB ETC1S versus 61.06 dB UASTC. Already
compliant source maps are kept at their authored resolution and codec. Scaled maps
are cached by source and size, then maps whose full decoded pixels match at 6-bit
precision share one encoded KTX2 image (maximum source difference 3/255 per channel).
`manifest.json` lists these shared images on each visual entry and verifies their hashes.
It never invokes `optimize`, simplifies L0 geometry, or quantizes positions.
The packaged GLB is decoded and compared, and L0 brotli size is limited to 8 MB at quality 5, matching the local server.

500 m and 1 km parents combine roofs from every published building layer in their footprint
with an ortho-draped DTM. When complete cached maanpinta LAZ and 2022 ortho coverage exists,
the assembler samples a 15.625 m visual-only DTM and embeds a 1024² ortho crop. This fills the
entire 1 km parent even when only the four Phase 1 L0 tiles have been published. Core input
coverage is required; it does not invent terrain beyond the observations. With no complete
raw coverage, parents retain available published ground, with coarse-only simplification and
locked boundaries. Structures-only inputs omit empty parent content. Raw LAZ, VRT and image
dependencies participate in the content hash; all writes remain in the sandbox.

The tileset is a REPLACE quadtree. Missing 500 m sibling quadrants receive coarse terrain
leaves when cached coverage is available, so refining the AOI does not remove its surroundings.
Bounds include whole objects and descendant content,
even when they extend outside nominal grid cells.

Inputs, implementation, texture contents and tool lock are hashed. An unchanged, verified
build writes nothing (including manifest timestamps). Changed/corrupt outputs rebuild;
removed collision entries disappear from the manifest. Texture encoding is cached separately
under the output's sibling `.texture-cache`, so parents reuse encoded images. Manifests are
published last, after staging and validation. Only one assembler may publish to a dist tree
at a time, and producers must finish their layer manifests before assembly.

## Region batch handoff (W2-02)

After all layer producers finish, invoke assembly once with the complete tile list:

```sh
source ../sandbox.env
bash pipeline/env/run.sh python -m turku3d.package.assemble \
  --area office-block --layers "$TURKU3D_WORK/build/layers" \
  --out "$TURKU3D_WORK/dist/tiles" --tile t0_0_0 t0_0_1 t0_1_0 t0_1_1
```

Exit 0 means publication and geometry/hash/budget/seam checks succeeded. Stdout is a JSON
object with `manifest` (absolute path) and `changed`; these fields also exist on a cache hit.
`manifest.json` is the existing `turku3d-manifest` v1 contract: `visual` is keyed by tile ID
(filter `level == 0` for L0); `collision` is keyed by collision ID with relative `url`,
`sha256`, `bytes`, `tris`, `prims`, `h`, and `flags`. Resolve URLs against the manifest's
directory. Entries from previous batches remain present. A collision URL's parent is its
L0 validation job directory (`collision/t0_i_j`). Do not launch concurrent assemblers.

Run independent Rapier jobs **after** that command succeeds, for example in Bash:

```bash
mkdir -p "$TURKU3D_WORK/reports"
printf '%s\n' t0_0_0 t0_0_1 t0_1_0 t0_1_1 | xargs -n 1 -P 4 bash -c '
  node tools/validator/src/cli.ts --walkers 2000 \
    --out "$TURKU3D_WORK/reports/validate-$1.json" \
    "$TURKU3D_WORK/dist/tiles/collision/$1"
' _
```

`xargs` returns nonzero when any validator fails. W2-02 can instead schedule these same jobs
from manifest URLs; preserve each validator's exit status and report, and never treat a failed
assembly as a validation-ready batch. Visual-only tiles have no collision validation job.

## Synthetic integration fixture

This is an explicit test-data operation; use an empty fixture directory. It contains flat
textured ground and exercises all 64 collision tiles and both coarse levels, not real Turku.

```sh
bash pipeline/env/run.sh python pipeline/tests/fixtures/make_layers.py \
  --out "$TURKU3D_WORK/build/fixture-layers"
bash pipeline/env/run.sh python -m turku3d.package.assemble \
  --layers "$TURKU3D_WORK/build/fixture-layers" --out "$TURKU3D_WORK/dist/fixture-tiles" \
  --tile t0_0_0 t0_0_1 t0_1_0 t0_1_1
make game-build
cd game && E2E_TILE_PORT=$((TURKU3D_PORT_BASE + 1)) TILE_DIST="$TURKU3D_WORK/dist/fixture-tiles" \
  npx playwright test -c playwright.tile.config.ts
```

Native dependencies are installed by `make pipeline-bootstrap`. The Node bridge uses the
shared pinned `~/turku3d/tools/npm`; `TURKU3D_GLTF_TOOLS` can select another installed copy.

## Office start and integration regression

When the buildings footprint/facade inventory covers LR15, assembly publishes optional
`manifest.spawn`: engine-local `position`, compass `headingDeg`, `vehicle.position` and
`vehicle.yaw` (radians, chassis +X forward). The player uses the nearest eligible walkable
ground triangle centroid outside all footprints on the outward side of a street-facing
facade. The car uses mapped public OSM parking with a full 5 × 2.2 m footprint clear of
buildings; private/customer-only parking is excluded. OSM input hashes participate in the
assembly cache. Missing public parking fails assembly instead of parking on the sidewalk.
`spawn.footprints`, `building_id`, `facade_id`, and vehicle OSM metadata retain QA provenance.
Explicit `?spawn=E,N,heading` continues to override the published start.

If `traffic_static/placements.json` is published, assembly writes all parked cars in
the requested L0 tiles to `manifest.vehicles` and each model/paint GLB to
`manifest.vehicle_models`. The packaged visual and collision tiles omit that layer's
static car geometry; the game creates sleeping rigid vehicles from these entries.
Their original layer boxes remain available for placement and source validation.

```sh
source ../sandbox.env
make tile-build T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
make game-build
(cd game && npx playwright test -c playwright.tile.config.ts integration.spec.ts --workers=1)
```

The office test verifies the footprint exclusion, L0 triangle count, draw-call budget,
wall pixels in the ID pass, car entry and console errors. It writes street, facade, car
and aerial screenshots under `$TURKU3D_WORK/reports/screenshots/integration-*`. The synthetic
ground-only suite skips this real-office test; synthetic unit tests cover spawn and batching.

## Ground texture binding

`--ground` defaults to `WORK/textures/ground`. Version-1 `material.json` must include ground-input
hashes matching the current `layers/ground/*.col.npz`; ortho-only previews and stale splats keep
library fallbacks. A tile material spec with missing shared PBR arrays fails assembly with the
missing paths instead of silently shipping flat ground. Build the arrays with `make ground-library`
in the sandbox before `make tile-build`. The assembler copies the three runtime KTX2 maps and shared eight-layer PBR
arrays to content-addressed `ground/` URLs and declares scene extras `ground_binding` with
`version`, `spec`, `library`, and `texture_bytes`. URLs are relative to the tileset.
The manifest records `ground_texture_bytes` and `wire_bytes`; L0 geometry plus these maps must
fit 8,000,000 bytes. The shared array library is fetched once across tiles. See
[ground rendering](../../../../game/src/render/ground/README.md) for complete scene capture commands.

Unmapped building facades use the same cached 28-module procedural atlas as drafts. The binder
keeps approved per-wall maps first, exports shared KTX2 images, and records `facade_kit` material
parameters for the game's WebGPU/WebGL2 node material; roof ortho bindings remain independent.
