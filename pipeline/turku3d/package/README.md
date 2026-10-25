# Layer assembly and packaging

Production consumes the complete manifests under `$TURKU3D_WORK/build/layers/`.
Publish layers first with [`turku3d.layers`](../../../docs/specs/layers.md).
Missing optional layers are fine; a requested tile needs at least one visual layer.
There is no automatic stub or raw-data fallback.

```sh
make tile-build T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
make tile T="t0_0_0 t0_0_1 t0_1_0 t0_1_1" TURKU3D_PORT_BASE=5290
make tile-test
make game-test
```

`make tile` assembles, validates with Rapier, then runs Chrome WebGPU/WebGL2 tests.
Output is `$TURKU3D_WORK/dist/tiles`; reports are `$TURKU3D_WORK/reports`.
The test browser uses `$TURKU3D_BROWSER_DIR`, on port base + 1 (5291 above).
To use an explicit layer inventory or texture library:

```sh
bash pipeline/env/run.sh python -m turku3d.package.assemble \
  --layers "$TURKU3D_WORK/build/layers" --out "$TURKU3D_WORK/dist/tiles" \
  --library textures/library --facades "$TURKU3D_DATA/textures/facades" \
  --tile t0_0_0 t0_0_1 t0_1_0 t0_1_1
```

Collision merges preserve all triangles, per-face material IDs, and primitives. Positions
are converted from float64 world-local into tile-local float32; every output is decoded,
checked against the source within 2 mm, and limited to 1 MB. Continuous-layer seams are
checked bitwise before and after conversion, including neighbouring previously shipped tiles.
Complete objects are not clipped or duplicated. `AOI_BORDER` follows the available collision
inventory, and changes on existing neighbours when new tiles are added.

Visuals preserve L0 topology and object IDs. The assembler binds library albedo/normal/ORM
maps and unique building facades, falling back to the library material when a facade is absent.
Untextured inputs retain their source colors. PNG/JPEG maps become ETC1S albedo or UASTC +
Zstandard linear maps through KTX-Software 4.4.2 **before** lossless meshopt compression.
It never invokes `optimize`, joins L0 meshes, simplifies L0 geometry, or quantizes positions.
The packaged GLB is decoded and compared, and L0 brotli size is limited to 8 MB.

500 m and 1 km parents include all published ground and roofs in their footprint, including
available L0 layers outside the requested tile list. Ground preserves its supplied ortho UVs
and images, with coarse-only terrain simplification and locked boundaries. Parents do not
invent terrain where no ground layer exists. The tileset is a REPLACE quadtree; bounds include
whole objects and descendant content, even when they extend outside nominal grid cells.

Inputs, implementation, texture contents and tool lock are hashed. An unchanged, verified
build writes nothing (including manifest timestamps). Changed/corrupt outputs rebuild;
removed collision entries disappear from the manifest. Texture encoding is cached separately
under the output's sibling `.texture-cache`, so parents reuse encoded images. Manifests are
published last, after staging and validation. Only one assembler may publish to a dist tree
at a time, and producers must finish their layer manifests before assembly.

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
cd game && E2E_TILE_PORT=5291 TILE_DIST="$TURKU3D_WORK/dist/fixture-tiles" \
  npx playwright test -c playwright.tile.config.ts
```

Native dependencies are installed by `make pipeline-bootstrap`. The Node bridge uses the
shared pinned `~/turku3d/tools/npm`; `TURKU3D_GLTF_TOOLS` can select another installed copy.
