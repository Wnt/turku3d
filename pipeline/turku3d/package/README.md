# Layer assembly and packaging

Production consumes the complete manifests under `$TURKU3D_WORK/build/layers/`.
Publish layers first with [`turku3d.layers`](../../../docs/specs/layers.md).
Missing optional layers are fine; a requested tile needs at least one visual layer.
L0 and collision have no stub or raw-data fallback. Coarse parents may read the cached
maanpinta ground points and 2022 ortho to fill their complete footprint.

```sh
make tile-build T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
make tile T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
make tile-test
make game-test
```

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

Visuals preserve L0 topology and source object IDs. Compatible primitives are batched per tile, layer and material.
Batches carry `extras.objects` with source metadata and index `start`/`count` ranges; the QA ID
pass expands these ranges into draw groups only while capturing IDs. Normal rendering uses one
draw per batch. Texture and material deduplication prevents repeated glTF texture transforms
from exhausting the streaming cache before L0 can load. The assembler binds library albedo/normal/ORM
maps and unique building facades, falling back to the library material when a facade is absent.
Both texture defaults are under `$TURKU3D_WORK/textures/assembly`. Until W1-09's assets are
promoted, pass `--library /Users/wnt/turku3d/wt/w1-09-facades/work/textures/assembly/library`
and `--facades /Users/wnt/turku3d/wt/w1-09-facades/work/textures/assembly/facades`.
Only W1-09's filtered export may supply unique facades: its parent `MANIFEST.json` must
have `bound` facade IDs (`<building_id>_<facade_index>`) and an `inventory_sha256` matching the current
`layers/buildings/facades.geojson`. Missing or changed geometry keeps the library fallback.
Raw staging is rejected. Unlisted indices are ignored even when matching files exist.
Sidecars must identify the building/index, declare metre UVs, pass `qa.gltf_basisu_valid`,
and supply all three `files` and matching SHA-256 `hashes`. Changed exported maps fail assembly.
`uv.scale`/`uv.offset` apply through `KHR_texture_transform` to albedo, normal, occlusion
and metallic/roughness, with clamped sampling. Library `material.json` `uv_scale` applies
to all library maps with repeating sampling; its default is `[1,1]`. Embedded source maps
retain their own UVs and transforms when no replacement map exists.

The single fallback table is `materials.mjs`: W1-03's four `facade_*` classes use
`render_smooth`; its four `roof_*` classes use `roof_standing_seam_metal`.
`plinth_granite`, `ground_asphalt`, and `ground_granite` use their namesake library assets.
Brick, wood and roof tile remain explicit generic approximations. Original `material_key`
is retained in node/material extras; `library_material_key` records the chosen library key.
Untextured inputs retain their source colors. PNG/JPEG maps become ETC1S albedo or UASTC +
Zstandard linear maps through KTX-Software 4.4.2 **before** lossless meshopt compression.
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
library fallbacks. The assembler copies the three runtime KTX2 maps and shared eight-layer PBR
arrays to content-addressed `ground/` URLs and declares scene extras `ground_binding` with
`version`, `spec`, `library`, and `texture_bytes`. URLs are relative to the tileset.
The manifest records `ground_texture_bytes` and `wire_bytes`; L0 geometry plus these maps must
fit 8,000,000 bytes. The shared array library is fetched once across tiles. See
[ground rendering](../../../../game/src/render/ground/README.md) for complete scene capture commands.
