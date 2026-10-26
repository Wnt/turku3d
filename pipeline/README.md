# Local data pipeline

The complete pipeline runs on the Mac. Shared inputs: `$TURKU3D_DATA/raw`
(default `~/turku3d/data/raw`). Workers only read them. Writable products:
`$TURKU3D_WORK/{build,cache,dist,reports}` (default `~/turku3d/data`).
See [env/README.md](env/README.md) for the native arm64 toolchain.
Shared point access, profiles, surfaces and relief detection are documented in
[the lidar toolkit](turku3d/lidar/README.md).

```sh
make pipeline-bootstrap pipeline-versions
make pipeline-test                        # offline, synthetic fixtures, 4 pytest workers
make fetch-aoi AREA=work-3x3               # prepare shared data once, intentionally writes raw/
make fetch-checksums AREA=work-3x3         # export small provenance files for git
make refresh                              # rebuild stale derived layers and bindings
make tile T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
make tile-show                            # sandbox port base + 1
make qa-refs                              # regenerate crops with corrected grid heading
make collision-audit                      # layer collider/visual check near published spawn
make collision-sweep                      # headless KCC grid sweep + ortho overlay
```

`make refresh` builds ground, buildings, bridges, props and the ground/facade
texture bindings in the sandbox. Before a stale product is rebuilt, a linked sandbox
directory is copied locally so the producer cannot write into shared live data.
Bridge layers are rebuilt when their declared source hashes
or code change. `make tile-build` requires the ground, buildings and bridges
manifests in the sandbox; run `make refresh` first when one is missing.
Structures remain a separate `make structures-build` step until its real AOI
acceptance report passes.

`make tile` checks the cached input registry without network access, builds locally, verifies
manifest hashes and every shared collision seam, runs the Node/Rapier validator and Chrome e2e
on WebGPU and WebGL2. Missing inputs fail with the exact preparation command.
The four office tiles stay the playable Phase 1 target; `work-3x3` is the larger input reservoir.

`collision-audit` reads `$TURKU3D_WORK/build/layers` and the spawn in
`$TURKU3D_WORK/dist/tiles/manifest.json`; it writes `reports/collision-audit/issues.json`
and `map.png`. It compares sampled solid wall triangles and every solid primitive against
their layer GLB, including GPU-instanced vegetation, and flags steep geometry labelled
as walkable pavement. It exits nonzero when candidates remain. `collision-sweep` uses the same
published `.col` files and the game's Rapier character controller to walk a 5 m grid
through the 40 m spawn circle, with a 2 m grid within 12 m. Its `sweep.json` records
movement stops; `map.png` overlays them on the 2025 ortho. Red marks need inspection,
orange marks a visible wall, building, curb or street prop. Unexplained stops make the
sweep fail. `make playtest` includes this real-tile sweep; run `make collision-audit-test`
for the synthetic fixture.

`turku3d.config` is the only coordinate/grid definition: EPSG:3877 + N2000, engine origin
E=23459500, N=6704500, H=0; x=east, y=up, z=south. Visual L0 is 250 m, collision is 62.5 m.
`data_root()`, `work_root()`, `raw_dir()`, `build_dir()`, `cache_dir()` and `dist_dir()` implement the W0-A contract.

```sh
make foundation-where E=23459668 N=6704254
make foundation-areas
bash pipeline/env/run.sh python -m turku3d.config --area-yaml work-3x3
```

The Phase 0 builder still produces coarse heightfield ground with orthophoto textures;
curbs, structures and building shells belong to the Wave 1 layers. It caches one canonical
area heightfield keyed by source file identities and interpolation code. Independent L0 builds
slice that same grid, avoiding repeated LAZ decompression/TIN construction and seam steps.

## Selective quality checks

From the repository root, run `scripts/dev/gate --base HEAD` for local changes or
`scripts/dev/gate --all` for the complete offline suite. See the [quality gate guide](../scripts/dev/README.md)
for dependency setup, path selection, caches, sandbox ports and Git hooks.

The [buildings layer](turku3d/buildings/README.md) reads both city LoD2 and roofer LoD2.2,
compares final snapped shells against class-6 lidar, and writes facade-ready layer artifacts:

```sh
make -j2 buildings-test                   # synthetic geometry and Rapier regression tests
make buildings-build                     # full build; nonzero if shell coverage fails
make buildings-validate                  # 2,000 walkers against the exported NPZ triangles
```
Wave 1 structures: [turku3d/structures/README.md](turku3d/structures/README.md).
`make -j2 structures-test` checks synthetic geometry, layer seams and Rapier traversal;
`make structures-build` consumes the completed ground layer and writes intermediate
structures plus an explicit acceptance report.
`make refresh REFRESH_TARGET=facades` rebuilds stale building shells and migrates
approved facade maps by building ID and wall geometry. The assembly export keeps
`APPROVED_INVENTORY.geojson` as the approved source geometry; the first refresh
captures it from the existing building layer before rebuilding. Reindexed, moved,
and split walls reuse their KTX2 maps with UVs projected onto the new baseline.
The migration report is `$TURKU3D_WORK/reports/facade-refresh.json` and lists
genuinely new street walls, ambiguous matches, and maps rejected because the
rebuilt wall no longer fits the approved photo. Approved maps
without a safe successor are retained under `textures/assembly/retired/<inventory-hash>/`.
`make refresh` runs only in a sandbox, detaches linked output directories when a producer
needs to rebuild them, and prints the cached/rebuilt status and elapsed seconds for each step.
Rerunning it after a failure reuses products whose declared inputs and code are unchanged.
