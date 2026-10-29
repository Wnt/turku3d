# Ground orthophoto cleanup

The draft's `ground_ortho` stage cleans the aerial RGB before ground visual binding.
Roof imagery retains the original source. Surveyed city surfaces override OSM
surface/road/parking polygons; lawns keep their green. Water and building footprints
are excluded. The primary canopy mask uses available classified lidar class-5 cells
above the ground grid by 1.5 m, with green/dark foliage colour as a backup. Missing
classified lidar does not trigger a download. Its half-metre canopy reduction reuses
the vegetation cache, outside the structural first pass.

Compact colour/brightness outliers on road and parking surfaces must fit car-sized
axes and area. Nearby mapped road headings support travel, perpendicular and diagonal
parking. Narrow lane lines and crossing bars fail the width/shape tests; explicit
mapped paint is excluded. This is a conservative heuristic, not semantic vehicle
recognition: ambiguous/low-contrast vehicles can remain.

Each masked pixel uses a clean donor of the same surface class within 12 m. Classes
with fewer than 32 clean pixels or more distant support use their deterministic
procedural material. Bright paint is excluded from donor pixels. No model runs at
generation time. Content hashes isolate the cleaned image from roofs and other
geometry stages; manifest `ground_ortho_cleanup` records pixels, canopy, vehicles
and total masked share by class. The cleanup producer is visual-only for structural
freshness.

Verification:

```sh
source ../sandbox.env
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 bash pipeline/env/run.sh python -m pytest -q \
  pipeline/tests/test_ortho_cleanup.py pipeline/tests/test_coarse_surfaces.py
```

The initial central-tile mask measurement is retained under the task's
`work/reports/ortho-cleanup.json`. Colour calibration is a separate delivery.

## Central-tile measurements

Shares below are masked pixels divided by all pixels assigned to that class,
including excluded building/water pixels in the denominator. Vehicle masks are
heuristic proposals, not independently labelled vehicle counts. Source preparation
and first-time classified-LAZ reduction are excluded from the mask-stage timing.
The second measurement reused those cached inputs and took 13.4 s for all three tiles.

| Tile | Mask stage | Road | Cycleway | Sidewalk | Paving slabs | Cobblestone | Quay |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| t0_0_0 | 2.78 s | 9.07% | — | 32.62% | 45.15% | 44.02% | 24.84% |
| t0_0_1 | 3.95 s | 19.30% | 27.89% | 37.69% | 34.25% | 21.96% | 32.57% |
| t0_1_0 | 3.92 s | 21.07% | 47.74% | 32.42% | 42.60% | 48.17% | 36.26% |
