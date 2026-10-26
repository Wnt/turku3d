# Office tile draw calls and walking height

The four L0 tiles were freshly assembled from the published ground, buildings,
props, vegetation, and parked-car layers with `make tile-build T='t0_0_0
t0_0_1 t0_1_0 t0_1_1'`. The local assembly produced nine visual tiles and
64 collision tiles. The unpublished structures layer was not an input.

## Draw-call diagnosis

At the office street spawn, the renderer selected three L0 tiles. Their loaded
scene graph had 151 colour meshes: 59 buildings, 35 props, 24 ground, 21 trees,
and 12 parked cars. The largest material groups were facade plaster (29),
facade brick (12), facade wood (8), signs (17), tree bark (12), tree foliage
(9), and parked-car paint (12). `renderer.info.render.drawCalls` reported 1,472
on WebGPU at 2560 × 1440 before the change.

Sequentially disabling each layer's shadow casting in the live scene removed
these calls:

| Layer | Shadow draws removed |
|---|---:|
| Ground | 126 |
| Props | 210 |
| Parked cars | 72 |
| Vegetation | 126 |
| Buildings | 354 |
| **Total** | **888** |

The remaining 584 draws came from the colour, prepass, reflection and screen
passes. The material batches from the assembler were intact; the regression
came from rendering each material batch again across shadow cascades and the
river reflection. The runtime now merges opaque non-ground tile geometry,
including instance transforms, into one shadow caster per loaded tile. The
original material meshes remain for colour and object IDs. Ground does not cast
shadows, and tile meshes use their own bounds for frustum culling.

After the change, the same three L0 tiles have three shadow casters and 151
colour meshes. Both WebGPU and WebGL2 report **432 total draws** at the office
spawn. The draw-call assertion remains `<500`.

## Walking height

The original explicit test coordinate (E 23459668, N 6704254) is inside the
office footprint. Collision data has a roof at 12.188 m and ground at 4.223 m
there, so a downward spawn ray correctly selected the roof. The walking fixture
now starts on the mapped sidewalk at E 23459685, N 6704256, clear of building
footprints along its northbound 8 m route. The WebGPU run walked 8.31 m and
changed height from 3.780 to 3.839 m. The existing `<2 m` assertion is intact.

## Commands

```sh
make tile-build T='t0_0_0 t0_0_1 t0_1_0 t0_1_1'
TURKU3D_JOBS=1 make tile-e2e
cd game && npx vitest run tests/unit/shadow-batch.test.ts
scripts/dev/gate --base main
```

The selected gate passed in 607.925 s, including the game browser stage in
579.225 s. The tile Playwright result was `passed` with no failed tests.

## Growing facade export

The final shared filtered export has 316 authorized street facades. A package-time
texel density cap keeps full-height facades at up to 48 pixels per metre and
short ground-floor facades at up to 72 pixels per metre, bounded by the source
size. For an over-budget tile, the assembler ranks facades by distance from the
office spawn, estimates savings from their exported KTX2 sizes, and reduces the
farthest facades first. It measures the resulting Brotli GLB plus ground PBR
arrays and extends the reduced set until the unchanged 8,000,000-byte limit
passes. The export's KTX2 files and QA hashes remain untouched.

The codec choice was measured on facade `103277350D_45` at 176 × 772 pixels:

| Map | ETC1S PSNR / bytes | UASTC PSNR / bytes | Packaged codec |
|---|---:|---:|---|
| Albedo | 42.64 dB / 12,620 | 58.88 dB / 43,994 | ETC1S |
| Normal | 29.09 dB / 11,071 | 61.06 dB / 63,248 | UASTC |
| ORM | 45.27 dB / 11,057 | 55.93 dB / 31,268 | ETC1S |

Full-size source maps retain their authored codec. Resized maps use these
choices. Maps with identical 6-bit pixels at the same dimensions and slot
share one encoded KTX2; that key allows at most 3/255 source difference per
channel. The final tile manifest records `budget_before_bytes` and
`wire_bytes`, both including each tile's ground texture bytes.

The pre-refresh build used all 316 bound facades and the published ground PBR arrays.
Each total is the Brotli GLB plus its ground texture bytes:

| L0 tile | Before scaling | After scaling | Ground included |
|---|---:|---:|---:|
| `t0_0_0` | 20,177,399 | 7,538,798 | 4,375,921 |
| `t0_0_1` | 11,683,814 | 7,757,446 | 4,267,083 |
| `t0_1_0` | 11,114,636 | 7,568,067 | 4,412,848 |
| `t0_1_1` | 13,570,879 | 7,765,095 | 4,537,593 |

The unchanged cap is 8,000,000 bytes per L0 tile. The build published nine
visual tiles and 64 collision tiles.

## Facade colour batching on the 316-facade export

Before atlas packing, the office view made **744 draws** on each backend. Its
three visible L0 tiles had 254 separate photographic facade material meshes
(190 plaster, 39 brick, 12 wood, 13 concrete). Each unique map prevented the
material batcher from joining the colour geometry.

The assembler now packs each L0 tile's photographic facade maps into 2048-pixel
KTX2 atlas pages. Albedo, normal and ORM maps share a rectangle layout; edge
texels extend four pixels into each gutter for mip sampling. The facade's metre
UV transform is baked into its mesh UVs with clamped source sampling, then the
existing batcher joins geometry by atlas material. Its `extras.objects` ranges
still identify each source facade for the QA ID pass. The maps are resized
directly from the source KTX2 and encoded once per page, avoiding individual
variant encodes that the atlas would discard. ETC1S is used for albedo and ORM,
and UASTC for normals, as in the measured choice above.

The office view now makes **457 draws** on WebGPU and **457 on WebGL2** at
2560 × 1440 after refreshing the derived ground layer on the rebased branch.
The ground refresh adds a `ground/wall` material; the three visible tiles still have three `buildings/facade_atlas`
colour meshes. The `<500` assertion and facade ID-pixel assertion are unchanged.
The initial three-worker browser run passed 11/12 cases but timed out on the
WebGL2 aerial QA navigation under host contention. Main subsequently added two
ground-texture QA views to the same test, causing the final screenshot to hit
its 120-second limit. The independent ground QA views are now a separate test
with all image and pixel assertions intact. On the rebased code,
`TURKU3D_JOBS=1 make tile-e2e` passed **16/16 cases in 2.6 minutes** against
the refreshed ground and all 316 facade bindings. The office and walking tests
wait for the requested L0 geometry before sampling asynchronous tile state;
the parked-car test waits for nearby collision tiles before driving. Facade IDs,
ground detail, parked driving, walking and tile seams pass on both backends.

The rebuilt 316-facade tiles, including ground PBR bytes, remain under the
unchanged 8,000,000-byte limit:

| L0 tile | Before adaptive scaling and atlas | Shipped with atlas |
|---|---:|---:|
| `t0_0_0` | 19,192,172 | 7,762,486 |
| `t0_0_1` | 11,565,815 | 7,862,962 |
| `t0_1_0` | 11,029,141 | 7,620,146 |
| `t0_1_1` | 13,360,623 | 7,846,973 |
