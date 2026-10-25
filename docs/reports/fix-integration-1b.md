# Office scene integration repair

The original assembled scene was stuck on its coarse roof/DTM parent. Browser inspection
measured `t2_0_0` at 2,866,002,264 estimated bytes against a 429,496,729-byte visual cache.
Repeated materials caused the glTF loader to clone transformed textures. The cache refused
L0 requests even though screen-space error correctly requested refinement. Raising the
cache limit or forcing L0 would have hidden the packaging failure.

The assembler now deduplicates textures/materials and batches compatible primitives per
tile/layer/material. Positions remain tile-local and L0 topology is preserved. Each batch
retains source object metadata and index ranges for the QA ID pass. A rebuilt office load
has 642,404 resident triangles and approximately 156 MB of visual cache. Walls were present
in the source L0 meshes; no wall winding, axis or SSE change was needed.

The default start comes from the nearest eligible ground triangle centroid outside all
building footprints, on the outward side of an LR15 street-facing facade. Its heading faces
the facade. The initial measurement is E 23459673.337, N 6704247.494, H 3.695, on sidewalk
material, replacing the address-point roof spawn at H 13.2. Explicit URL spawn overrides
remain available for tests and developer routes.

Local OSM way 4690693 marks the frontage `parking:both=no`. Vehicle placement therefore
uses mapped public parking, excludes private/customer-only areas, and requires a complete
5 × 2.2 m body footprint inside the parking polygon and outside buildings. The manifest
retains parking source/ID/tags and footprint provenance. Both player and car collision tiles
must finish loading before initial ground queries.

Coplanar water tiles share their river reflection. Reference ownership transfers the target
to a surviving tile on unload and disposes resources after the last tile releases them.
This avoids repeated reflection scene/shadow renders for the same river plane.

## Reproduction and deployment

After the orchestrator lands the branch, use the deployment sandbox environment:

```sh
make tile-build T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
make game-build
make tile-show
```

`make tile-show` serves the rebuilt game and tiles on that sandbox's base + 1 port.
For this worker, the port is 5481; main's documented deployment port is 5171.

```sh
source ../sandbox.env
scripts/dev/gate
(cd game && npx playwright test -c playwright.tile.config.ts integration.spec.ts --workers=1)
```

The office regression checks footprint exclusion, non-roof spawn, >100,000 resident
triangles, visible L0, <500 all-pass draw calls, >100,000 facade pixels in a 1440p ID image,
car entry, and no console errors. It captures street, facade, car and aerial views.
The original shared build fails this test because the required safe-spawn metadata is absent.
Synthetic ground-only suites explicitly skip real-office acceptance.

Focused verification: 24 packaging tests passed in 28.63 s and eight rendering unit tests
passed in 1.76 s. The first gate's browser stage passed on both backends; its two cache-test
failures occurred while producer code was still being edited, and the stable package rerun
passed without changing assertions. Final gate and office evidence are recorded below.

## Final measured result

The full selected gate is **GREEN: 304.102 s**, including both browser backends. Host
contention dominates this full browser run; the stable focused packaging suite took 28.63 s.
Final rebuild published nine visuals and 64 collision tiles; all manifest hashes pass.
The vehicle is at engine (218.2387, 4.3495, 181.6285), in public OSM parking area 4359236,
approximately 84 m from the player. It is not parked on the prohibited LR15 frontage.

At 2560 × 1440, medium quality, WebGPU at the default office start measured:

| Metric | Measured |
|---|---:|
| Resident triangles | 642,404 |
| All-pass draw calls | 355 (original screenshot: 3,468) |
| Frame interval p50 / p95 | 16.665 / 17.520 ms |
| CPU p50 / p95 | 3.895 / 4.490 ms |
| GPU p50 / p95 | 12.775 / 13.318 ms |
| CPU + GPU p50 / p95 | 16.616 / 17.443 ms |

The median is approximately 60 fps; this is not a claim that every frame meets 16.67 ms.
The car view captured 548 draw calls at approximately 59 fps. WebGL2 remains a functional
fallback, measuring approximately 30 fps at 1440p; the requested performance target is WebGPU.

Evidence is in `/Users/wnt/turku3d/wt/fix-integration-1b/work/reports/`:

- `gate-final.log`, `package-tests-final.log`, `render-unit-final.log`
- `baseline-regression.log`, `office-e2e.log`
- `screenshots/integration-webgpu-street.png`
- `screenshots/integration-webgpu-facade-color.png`
- `screenshots/integration-webgpu-facade-id.png`
- `screenshots/integration-webgpu-car.png`
- `screenshots/integration-webgpu-aerial.png`

Visual inspection confirms L0 walls, roofs, river and detailed ground geometry. This input
inventory still uses generic facade library materials and plain ground materials. Detailed
facade imagery and ground texture products belong to the concurrent W1-09/W1-10 delivery;
the screenshot evidence must not be described as completed photorealistic facade work.

The dedicated office e2e passed **2/2 in 2.2 minutes**, with one worker: WebGPU 28.8 s,
WebGL2 approximately 1.3 minutes. Both verified the facade pixel budget, car entry and zero
console errors, and captured all requested views. The branch was rebased onto main after
the orchestrator landed the two functional commits. The incoming vegetation integration
required preserving `EXT_mesh_gpu_instancing` nodes in the batcher; ordinary shared mesh
primitives are cloned before baking transforms. The existing instancing/world-bounds
regression passed (1 test, 3.46 s). These are packaging compatibility changes; the measured
input inventory contains ground and buildings only.

After the instancing compatibility change, the scoped gate is **GREEN: 24.739 s**
(packaging tests, JS lint, knip, generated data and policy). No runtime rendering source
changed after the full gate and office screenshots. Main contains the spawn/reflection
change as `f53dcad`; the original worker commit was `8bf6461`. The original cache/batching
fix is on main as `9d9eba5` (worker `b153768`).
