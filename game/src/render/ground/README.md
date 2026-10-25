# Ground material API (W1-10 → W1-08 / W1-04)

`material.ts` exports `loadGroundLibrary`, `loadGroundTile`, and `createGroundMaterial`.
It does not change the renderer, environment, or streaming callbacks owned by W1-08.

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

Close range (≤8 m) uses material detail, with 30% low-frequency ortho colour. A smooth transition
ends at 45 m, where the original geographic layout comes from the cleaned ortho. Three translated
samples on a triangular lattice suppress repeated patches. Shared lattice values and explicit
texture gradients are evaluated before material branches: TSL's cached expressions otherwise
leave nonzero material channels uninitialized. Translation preserves normal orientation. Concrete slabs quantize those translations to
whole 50 cm slabs (four per 2 m texture), preserving the paving grid rather than blending
misaligned joints.
Splat samples are renormalized after lossy decoding. Detail normal/roughness/AO fade with colour.

Road paint is separate vector geometry, not baked into the library. W1-04 must intersect
`markings.geojson` with the ground triangles, triangulate and offset it 5 mm, preserving each
`object_id` for QA. Use an opaque off-white rough material, polygon offset, and the terrain's
normals. Never drape paint over buildings or across water. The current extractor emits crossings
only when OSM provides line geometry and clips to city carriageway polygons. Lane and stop lines
need verified source geometry; missing types are reported, not guessed.

## Tests and screenshots

From the repository root:

```sh
source ../sandbox.env
make -j2 ground-test
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
