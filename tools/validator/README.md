# Tile physics acceptance gate

Node 24, Rapier 0.21 WASM and the shared `.col` reader. The validator imports
`game/src/physics/vehicle.ts`; `PhysicsWorld` uses that same module. No vehicle
physics are copied into the validator. The browser and Node pass their Rapier
module into `GameVehicle`.

Run from the repository root, with the sandbox environment exported:

```sh
make col-deps PIPELINE_PY=$HOME/turku3d/envs/pipeline/bin/python
make col-test-ts PIPELINE_PY=$HOME/turku3d/envs/pipeline/bin/python COL_RT_DIR=$TURKU3D_WORK/roundtrip
make game-test
make tile-validate T="t0_0_0 t0_0_1 t0_1_0 t0_1_1" PIPELINE_PY=$HOME/turku3d/envs/pipeline/bin/python
```

`tile-validate` runs all requested visual tiles together, including their shared
seams, with four persistent worker threads. It writes
`$TURKU3D_WORK/reports/validate-aoi.{json,html}` and **fails when buildings, curbs,
stairs or cars are missing**. A Phase 0 ground-only build is not Phase 1 acceptance.
The CLI supports optional subsets for development; skipped checks fail when named
in `--require`. Exit codes: 0 pass, 1 failed or missing required checks, 2 input error.

```sh
node tools/validator/src/cli.ts --help
node tools/validator/src/cli.ts --layers "$TURKU3D_WORK/layers" \
  --require buildings,curbs,stairs,cars --workers 4 \
  --out "$TURKU3D_WORK/reports/validate-aoi.json" "$TURKU3D_WORK/dist/tiles/collision"
```

## Layer metadata

Each `layers/<layer>/MANIFEST.json` may advertise sidecars relative to itself:

```json
{"validation":{"footprints":"footprints.geojson","curbs":"curbs.geojson","stairs":"stairs.json"}}
```

The same filenames are discovered without manifest entries. `--footprints`,
`--curbs`, and `--stairs` override the corresponding discovered inputs. The JSON
report records all source paths. Inputs from multiple layers are concatenated.
Referenced missing/malformed files are errors, never silently skipped.

* Footprints: GeoJSON Polygon/MultiPolygon with stable feature `id` or `properties.id`.
* Curbs: GeoJSON LineString/MultiLineString with `properties.step` (or `height`)
  in metres. This independent expected height catches an accidentally raised
  collision curb; the measured height must differ by **less than 3 cm**. Every
  segment receives walker stations (2 m default) and a car strike at its midpoint.
* GeoJSON coordinates: engine `[x,z]` by default (`turku3d_coords: "engine-xz"`),
  or EPSG:3877 `[E,N]` (`turku3d_coords: "en"`). Mixed conventions in one input kind
  are rejected. Values must follow the project's N2000/world-local convention.
* Stairs: an array (or `{ "flights": [...] }`) of
  `{ "id": "flight-id", "bottom": [x,y,z], "top": [x,y,z], "width": 2 }`.
  These define test routes, **not colliders**. Missing/incorrect ramps therefore fail.

Without sidecars, curb faces and connected stair-slope triangles can be probed
from the shipped mesh. Footprints must come from the layers: deriving them from
the same possibly broken shell would hide missing buildings. Source stair lists
also retain whole flights that cross collision-tile boundaries.

## Checks and thresholds

| Check | Acceptance |
|---|---|
| `format` | CRC, section sizes, shipping materials, ≤150k triangles / ≤1 MB, origin/bounds, degenerates and duplicate tile IDs |
| `seams` | Every neighboring solid boundary has identical float64 world-coordinate bit patterns after `.col` decode, and identical triangle-edge partitions. Unmatched vertices and segments include exact coordinates; no rounding tolerance or skirts. Matching point sets alone do not excuse T junctions. |
| `holes` | Two jittered downward rays/m² hit solid trimeshes; primitives and sensors cannot conceal holes |
| `buildings` | 2,000 seeded walkers over the AOI, ten seconds each; every footprint gets walkers. Entry >5 cm into a footprint at walking height fails, including roofless/missing shells. Untestable footprints fail. |
| `curbs` | Every ≤25 cm curb climbs at 1.5 and 4 m/s; ≥35 cm blocks. The middle band is informational, but a mismatch with the layer's declared height fails. Missing surfaces or starts fail. |
| `stairs` | Every listed flight climbs up/down, reaches the landing within 10 cm, and has slope ≤40°. Fallback: each stair-slope mesh component. |
| `cars` | Exact game car coasts across every curb segment at 3/6/10 m/s. Pitch spike and speed loss are reported; ARCHITECTURE 1.14 response thresholds remain report-only pending W1-07. Steps >25 cm at 2 m/s must stop the chassis centre on the approach side (5 cm tolerance). At 30 m/s it must not cross a building/quay/wall face at any frame. |
| `belowGround` | Drops on a 5 m grid and samples every tenth frame of all walker trajectories must retain ground under their feet |

The KCC uses one movement per 60 Hz step, grounded vertical speed −0.5 m/s,
full gravity in air, radius .3, half-height .6, skin .02, autostep .22/.15, snap .3,
max climb 45°, slide 50°. `--kcc-mode split` is retained as an existing diagnostic;
its known 25 cm climb failure is pinned by the KCC tests.

Worker ownership is by collision tile; a full neighboring tile halo supplies
approach routes and continuous collisions. Hole/drop rays are limited to the
owned tile; building walkers are apportioned across footprints with a global
2,000 budget (at least one per footprint). Seam comparison runs globally before
partitioning. Workers release their Rapier worlds between jobs. Report version 2
contains `checks`, `summary`, `pass`, elapsed wall time and worker timing. Parallel
checks retain all details under `perTile`, including skipped tiles.

## Orthophoto failure map

Build a north-up backdrop directly from cached 2022 true ortho GeoTIFFs:

```sh
$HOME/turku3d/envs/pipeline/bin/python tools/validator/ortho.py \
  --root "$TURKU3D_DATA/raw/ortho/ortho2022to/work-3x3" \
  --bounds 0 0 500 500 --out "$TURKU3D_WORK/reports/ortho.png"
make tile-validate T="t0_0_0 t0_0_1 t0_1_0 t0_1_1" \
  PIPELINE_PY=$HOME/turku3d/envs/pipeline/bin/python \
  VALIDATOR_ARGS="--ortho $TURKU3D_WORK/reports/ortho.png --ortho-bounds 0,0,500,500"
```

The HTML embeds the image, collision grid, failure locations and complete JSON.
It works offline with no map service or API key. Hover a marker for its details.
PNG/JPEG/WebP are accepted; `--ortho-bounds` is mandatory with `--ortho`.
Without a backdrop the same report is a coordinate map. Failure samples are
bounded by `maxSamples` per check, but total failure counts are not truncated.

## Regression fixtures

Existing `fixtures/{good,neg_hole,neg_open_building,neg_steep_stair,neg_material0}`
cover the walkers and codec. `test/acceptance.test.ts` adds seam gaps and T
junctions, the exact game car versus a 0.1 m wall (including a deliberately
non-colliding wall), a 30 cm curb violating a 25 cm declaration, low-speed car
blocking, layer manifests, worker ownership and escaped offline reports.

```sh
$HOME/turku3d/envs/pipeline/bin/python tools/validator/test/generate_acceptance.py
make col-fixtures PIPELINE_PY=$HOME/turku3d/envs/pipeline/bin/python
```

The existing good fixture is a **walker** acceptance scene; car acceptance is
covered separately because raycast wheels have known curb limitations pending
W1-07. No physics threshold is relaxed to make that scene pass as a car scene.

## Buildings layer before assembly

`make buildings-validate` checks W1-03 layer NPZ triangles through `src/buildings.ts`;
no shipped `.col` is created. It uses this validator's `Walker`, 2,000 seeded ten-second
trials, and a temporary horizontal pad at each shell's base to isolate wall blocking.
Per-triangle `building_index` provenance indexes `metrics.json.collision_building_ids`.
Each trial enables only the target building's exported triangles. This gives nested parts
valid outside starts and prevents an enclosing building from hiding a broken inner wall.
Missing shells fail coverage and are listed in `missingShells`; existing shells still
receive the complete 2,000-trial run. Any entry into a footprint fails, even if the walker
later exits. This does not replace integrated terrain/vehicle/streaming validation.
Closed/open-wall and nested-part masking regressions are included in `make -j2 buildings-test`.

The gate runs all synthetic assertions with `FIXTURE_WALKERS=32`, two concurrent fixture workers,
and only the explicitly expected checks on each negative fixture. The good fixture still exercises
all checks. Standalone `node --test tools/validator/test/*.test.ts` retains 150 fixture walkers;
set `PIPELINE_PY=$PWD/.venv/bin/python` from the repository root for its codec generator.
`make tile-validate` retains full real-tile sampling and thresholds.

### Parked-car layer probes

`bash pipeline/env/run.sh python -m turku3d.parked.verify` validates the published
`traffic_static` placement audit and generates sandbox report `.col` fixtures.
It invokes `node tools/validator/parked.mjs <collision-directory>` to verify every
solid box using the shared reader/collider builder, player/vehicle interaction
groups, downward rays and capsule/chassis sweeps. Details and build commands:
[`pipeline/turku3d/parked/README.md`](../../pipeline/turku3d/parked/README.md).
