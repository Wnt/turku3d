# Ground material API (W1-10 → W1-08 / W1-04)

`material.ts` exports `loadGroundLibrary`, `loadGroundTile`, and `createGroundMaterial`.
`MaterialStyler` delegates assembler-declared ground bindings to `GroundBindings`. QA readiness and gameplay `state().tilesSettled` wait for these loads and the resulting batch. The binding owns tile maps, shares library arrays, and disposes late results after a tile unload.
After binding, ground meshes in the same tile share one world-space geometry and one
material draw. Their original meshes remain hidden until tile release, so the ID
pass can still use the unbatched source geometry on its separate QA page. The
four-tile Chrome sweep in `game/tests/e2e-tile/area.spec.ts` checks the resulting
draw budget at street poses and at the bridge gameplay spawn.
The office integration spec checks the published binding, applied runtime material, and distinct
ground pixels near the spawn on WebGPU and WebGL. Run it after `make tile-build` with
`cd game && npx playwright test -c playwright.tile.config.ts integration.spec.ts --workers=1`.

```ts
const library = await loadGroundLibrary(new URL('textures/library/', location.href).href, ktx2Loader);
const ground = await loadGroundTile(new URL('textures/ground/t0_0_0/material.json', location.href).href,
  ktx2Loader, library);
groundMesh.material = ground.material;
// When the tile unloads:
ground.dispose();
// When the entire library unloads (not for each tile):
Object.values(library).forEach(texture => texture.dispose());
```

The assembler copies the texture products to URLs relative to the metadata. Bind only meshes
whose layer is `ground`; buildings, water and markings retain separate materials. The API must
be called after KTX2Loader detects renderer support. World matrices must put the mesh in engine
x/y/z coordinates. Geometry UVs remain the layer contract's planar E/N metres (u east, v north).
The shader uses world x/z for ortho and splat lookup, and world E/N for library texture coordinates.
Do not subtract the tile origin again. A future floating-origin implementation must supply the
unrebased world position to this material before enabling origin rebasing.

Eight material channels, in order, are listed in `GROUND_KEYS` and must match
`textures/library/ground.json`. Each PBR map is one eight-layer KTX2 array; only six texture
samplers are required (three arrays, ortho, two splats). The library is shared by all resident tiles.
KTX2 images have north-first rows and `flipY=false`. Metadata supplies the padded image UV
transforms; do not use a tile's full [0,1] domain directly on the padded textures.

Close range (≤8 m) uses material detail. Orthophoto contributes only a bounded 0.96–1.06
low-frequency luminance multiplier, so aerial shadows and blue/white objects cannot tint the material. Material albedo remains authoritative at distance; mipmaps reduce detail without reintroducing
photographed shadows or objects. Normal/roughness detail fades between 8 and 45 m. Three translated
samples on a triangular lattice suppress repeated patches. Shared lattice values and explicit
texture gradients are evaluated before material branches: TSL's cached expressions otherwise
leave nonzero material channels uninitialized. Translation preserves normal orientation. Concrete slabs quantize those translations to
whole 50 cm slabs (four per 2 m texture), preserving the paving grid rather than blending
misaligned joints.
Splat samples are renormalized after lossy decoding. Detail normal/roughness/AO fade with distance; albedo retains the same material class.

Road paint is separate vector geometry, not baked into the library. W1-04 must intersect
`markings.geojson` with the ground triangles, triangulate and offset it 5 mm, preserving each
`object_id` for QA. Use an opaque off-white rough material, polygon offset, and the terrain's
normals. Never drape paint over buildings or across water. The current extractor emits crossings
only when OSM provides line geometry and clips to city carriageway polygons.

Draft tiles add rule paint from their roads.json v2 (`pipeline/turku3d/roads/paint.py`, phase G3): yellow
dashed centre lines on two-way major and tertiary streets (and residential streets tagged with two or
more lanes), white lane lines between same-direction lanes, white edge lines on 60+ km/h roads, zebras at
OSM crossing points (not `crossing:markings=no`/`unmarked`, not where an OSM crossing way already
paints one) and stop lines at signal stop positions, set back before zebras. Lines stop at junction
setbacks and at zebras; no paint on unpaved, living-street, pedestrian or tunnel edges. Dash phases
follow the logical edge so whole edges continue across tile seams. All paint stays one `ground_paint`
mesh per tile (`layer: markings`, `material_key: road_paint`, 8 mm lift): COLOR_0 multiplies the shared
off-white, giving the Finnish centre-line yellow without another draw. Paint for a bridge edge lies on
the built draft deck (its height profile + 12 mm, clipped to the tile; the bridge builder no longer paints
its own lane dashes); ground paint never lands on deck triangles. Paint receives shadows and casts none.

## Tests and screenshots

From the repository root:

```sh
source ../sandbox.env
make -j2 ground-texture-test
make ground-render-test                  # tiny synthetic arrays, actual Chrome Metal GPU
make ground-qa                           # requires all four real products + library
```

`GROUND_QA_PORT` defaults to 5310; set it explicitly outside this sandbox. Chrome uses
`$TURKU3D_BROWSER_DIR/ground-{webgpu,webgl}`. The QA runner owns and stops its Vite server.
Real screenshots and JSON reports go to `$TURKU3D_WORK/reports/ground-qa`; synthetic
regressions use its `synthetic/` subdirectory so they never overwrite acceptance captures.

The three real views use the registry's office x/z positions and headings, a 1.7 m eye, and a
fixed downward pitch. They show **isolated flat ground**, not the completed city or collision
geometry. Before is the 2022 ortho; after is the cleaned ortho/splat/library material. These
are material inspections; full in-game visual acceptance remains dependent on W1-01/W1-04/W1-08.
The synthetic GPU test also checks pixel variation in four material channels, so a valid but
flat shader fails the test.

## Assembled office-core verification

The assembler defaults `--ground` to `$TURKU3D_WORK/textures/ground`. It checks splat source hashes
against the actual ground layer and copies products to content-addressed URLs. Missing or stale
products retain the existing material. Scene extras `ground_binding` version 1 declares `spec`,
`library`, and `texture_bytes`, relative to the tileset URL. No runtime filesystem paths are guessed.
The assembler reports and enforces L0 geometry plus ground-map wire bytes against 8,000,000 bytes.

```sh
source ../sandbox.env
PYTHONPATH=textures:pipeline bash pipeline/env/run.sh python -m turkutex.ground.build \
  --ground-layer "$TURKU3D_DATA/build/layers/ground"
bash pipeline/env/run.sh python -m turku3d.package.assemble \
  --layers "$TURKU3D_DATA/build/layers" --tile t0_0_0 t0_0_1 t0_1_0 t0_1_1
node tools/dev/serve-dist.mjs --root "$TURKU3D_WORK/dist" --port 5314 --no-compress
# In another sourced terminal:
(cd game && GROUND_TILE_SERVER=http://127.0.0.1:5314 ./node_modules/.bin/vite \
  --config vite.ground.config.ts --host 127.0.0.1 --port 5313)
# In a third sourced terminal:
node game/scripts/ground-scene-qa.mjs http://127.0.0.1:5313/
```

This captures three office-core street views on both backends in `reports/ground-scene`.
Each camera is raycast onto real terrain and placed 1.7 m above it. Before/after geometry,
lighting and cameras match; the baseline removes only the ground binding from served GLBs.
The runner asserts both terrain and buildings are present, counts textured meshes, rejects console
errors, and records SHA-256 for each PNG. Close both servers after capture.

For the bounded gate used by this run (same checks, serial stages, two test workers):

```sh
TURKU3D_JOBS=2 PYTHONPATH=scripts/dev .venv/bin/python - <<'PY'
import tomllib
from pathlib import Path
from gate_lib import changes, run_gate
root = Path.cwd()
config = tomllib.loads((root / 'scripts/dev/gate.toml').read_text())
config['gate']['workers'] = 1
raise SystemExit(run_gate(root, config, changes(root, 'main')))
PY
```

For a timeboxed capture, append `before webgpu` or `after webgpu` to the scene runner command.
Separate mode runs preserve the other mode's manifest records. Omit both arguments for the full
three-view, two-backend matrix.
