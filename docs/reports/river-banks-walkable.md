# Lidar-fitted river walks

The coarse generator fits complete OSM riverside paths and paved pedestrian areas
to city ground returns. The fit is generic: there are no Aura way IDs, camera
coordinates or tile-specific geometry rules in the producer.

`coarse/walkable.py` prepares canonical eight-metre longitudinal knots and separate
paving/embankment cross sections. Dominant height bands initialize trimmed plane
fits; vegetation, buildings and water are excluded. Cached classified lidar is
preferred; surveyed ground key points support locations without that optional
source. Quarter-metre spatial/height bins prevent dense scan strips dominating.
Insufficient or steep/noisy evidence retains the existing terrain.

Paving edges and slope bands constrain the terrain before height assignment and
shoreline cutting. Both visual ground and collision use the same resulting mesh.
Mapped paving is protected from natural-bank lowering toward water. Exact paving
edge samples do not inherit missing adjacent slope values. Vector cutting retains
small valid polygon remainders rather than discarding submillimetre surface areas.
Complete source ways and fixed stations provide the same fit at neighbouring tile
edges. Existing retaining walls, garden terraces and final 3D tunnel-clearance
cuts retain their place in the generation sequence.

Ordinary mapped steps now receive the same lidar tread/landing survey as bridge
access stairs. Counted stair sections tagged as footways are also produced as
stairs and excluded from paving fits. Measured endpoint order sets ascent;
mapped bends and widths stay authoritative. Step counts are adjusted only where
necessary to keep risers walkable. The manifest records `stairs.lidar_flights`
and `walkable` path, support and length counts.

## Recorded quay measurement

The supplied camera is in `t0_-1_1`, beside the lower western quay. A 180 m reach
of the mapped path was sampled every 25 cm. The baseline uses the original
ground-key TIN and actual mapped quay/embankment barriers, before fitting. A dent
is deviation from the midpoint of a two-metre chord; a step is adjacent sample
height change, including the real longitudinal grade.

| Measure | Before | Fitted visual/collision source mesh |
| --- | ---: | ---: |
| Maximum height change over 25 cm | 116.4 mm | 1.14 mm |
| Maximum dent over 2 m | 334.7 mm | 1.62 mm |
| Missing vertical samples on the reach | 0 | 0 |

These are local source-mesh measurements, not a claim that every shipped tile has
been rebuilt. The deployed probe below separately checks the packed collision,
counts stairs, and records the tiles actually built.

## References and verification

The supplied recording and nearby 2019-06 Street View panorama
`s3JjTgeBcJ4rkWD1fRkqlg` show smooth paving and separate upper promenade,
embankment and lower quay. Reference crops stay in the sandbox. The retained
Google 3D round-3 capture `t0_-3_3_q10` provides a second downstream riverside
check; it has an accepted independent height calibration with 89.8 mm held-out
p90. On 371 mapped paving cells in that control, median/p90 absolute error is
21/53 mm for the original TIN and 38/101 mm after smoothing. This already-smooth
control therefore records the smoothing tradeoff as well as the large improvement
on the recorded damaged reach. Reference imagery and provider meshes are never
generator inputs or git assets.

Focused checks:

```sh
source ../sandbox.env
bash pipeline/env/run.sh python -m pytest -q \
  pipeline/tests/test_coarse_walkable.py pipeline/tests/test_coarse_banks.py \
  pipeline/tests/test_coarse_surfaces.py pipeline/tests/test_coarse_footbridge.py
bash pipeline/env/run.sh python -m pytest -q \
  pipeline/tests/test_coarse_sources.py pipeline/tests/test_coarse_latency.py \
  -k 'prepared_keys or structural_inputs or first_pass_keeps_structural or sampler_stage or collision_identity or surface_code or paving_rule'
python3 scripts/dev/probe_walkable.py --commit "$(git rev-parse HEAD)"
```

The selective deployed run builds only `t0_-1_1` and `t0_0_1`, first and full,
checking source building counts, all 16 collision cells and visual artifacts.
It then measures packed-collision walking rays and missing surface polygons
larger than 1 cm², excluding mapped stairs, bridges and building interiors from
the paving audit. It reports stair lidar endpoint corrections, sampled roughness,
audited area and actual build durations. The owning task imposes a ten-minute
hard deadline on this entire run, including slot admission.

## Deployed edge correction

The first OCI run built the recorded tile first/full in 14.293/28.699 s with
23 source buildings and all 16 collision cells. All 12 emitted stair flights
received lidar profiles; endpoint corrections reached 269 mm. Packed collision
on the recorded reach reduced maximum 25 cm change from 116.4 to 1.17 mm and
maximum 2 m dent from 327.1 to 1.19 mm, excluding stair approaches and buildings.

The area audit also found 20 real dry-edge holes totalling 3.378 m² across
923.86 m² of tested paving. Centreline rays alone missed these edge defects.
The paving exclusion from natural-bank soil grading exposed a material fallback:
a wet triangle centre incorrectly gave its dry cut remainder a water material.
The forward fix keeps the original ground material for every exterior remainder.
The regression now checks the exact dry and wet projected areas independently;
a river bed or water sensor cannot count as dry surface coverage.

The opposite-bank first pass reached complete collision in about 26 s, then
exceeded its 75 s budget inside existing neighbour wall validation. That separate
latency was reported to coordination; its assertions and budget are unchanged.
The single forward run repeats both affected first/full builds and area metrics.
