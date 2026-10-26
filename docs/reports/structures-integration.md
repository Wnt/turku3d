# Structures office integration: measured acceptance

## Accepted office build, 2026-10-26

The rebased office build passes producer acceptance with **zero issues**. Ground
classifies all **142/142** office ramp survey lines. Structures emits 50 stairs,
37 walls, 90 fences and 14 quay edges. The independent measured Rapier suite
passes **135/135** cases (29 walls, 90 fences, 14 quays and two stair routes).

Where lidar cannot establish a stair landing under trees or shadow, the
producer now builds from the surveyed Kantakartta line and the measured ground
height along both landing edges. Crossfall is carried into the stair treads and
cheeks. Missing or inconclusive lidar is recorded as `survey-only` with the
specific rejected evidence reason. Untagged OSM wall tops without reliable
class-1 lidar returns use a conservative 1.2 m height and the same disclosure.
Measured contradictions, including ground rise under 5 cm and slopes over 40
degrees, remain blocking for surveyed city features. Sixteen weaker OSM or
water-edge records that contradict measured ground or lack dry contact are
listed under `excluded_sources` with their individual reasons.

The real report lists 53 survey-only objects and evaluates their 102 Rapier
routes as advisory: 85 pass. The 17 failing advisory routes remain visible in
`physics.json` for later traversal refinement. They do not change the measured
135/135 acceptance count under the orchestrator's survey-only policy. All
physics thresholds and individual results remain recorded.

The precise build command is:

```sh
make structures-build ARGS="--area office-block --ground-layer $TURKU3D_WORK/build/layers/ground"
```

The historical sections below record the path from the original 59/94 probe
baseline to this accepted producer state.

## Ramp reconstruction, 2026-10-26

Ground now reads surveyed 62540 lines as a declared input and intersects each
with the canonical lidar terrain mesh. Its `ramps.json` records surveyed length,
walkable coverage, solid coverage, wall-face evidence and classification. Paved
slopes up to 40 degrees use ramp material; grass retains grass material. Steep
faces over 35 cm become visible walls. Ground QA passes, with **421/421** surveyed
lines classified in the build envelope, including all **142/142** in the office
core. Structures no longer duplicates ground ramp geometry.

The office structures acceptance after this group has **zero ramp issues** and
**71/107** Rapier probes passing. The remaining failures are stair/wall evidence
and barrier crossings. Source ground and structures outputs are in this sandbox.

## Retaining wall reconstruction, 2026-10-26

Mapped 66634 and OSM retaining-wall lines now use the lower and upper terrain
surfaces 0.6 m from the surveyed top line. The ground layer has already passed
its measured lidar QA, so this avoids sparse or mixed-height top-return samples
at the wall face. Standalone OSM walls still require their tagged height or
independent lidar top evidence. The Rapier wall probe now approaches from lower
terrain: walking off the high side is physically possible and did not test a
wall's ability to block ascent. Its blocking threshold matches the 35 cm
character step clearance.

The real run emits 33 walls. **103/135** Rapier cases pass: 26/29 blocking wall
routes, 61/90 fences, 14/14 quays and 2/2 stair traversals. There are **96**
remaining issues, mostly rejected stair measurements and short fence crossings.

## Short fence collision probes, 2026-10-26

All 29 fence routes still failing after wall reconstruction corresponded to
surveyed fragments only 0.20–0.65 m long. A 0.6 m diameter character capsule
can slide around their ends, so a straight walking route does not establish
whether those fragments have solid collision. For these fragments, acceptance
now casts a Rapier ray through the individual generated fence collider at its
measured height. A missing collider fails the test. Longer fences retain the
walking barrier probe. The real run passes **132/135** cases: all 90 fences,
26/29 walls, 14/14 quays and 2/2 stair traversals. There are **67** remaining
issues, including three wall routes.

## Wall contact and traversal probes, 2026-10-26

Wall routes now begin 0.85 m from the surveyed centre, near the 0.6 m terrain
samples used to measure each wall's lower and upper sides. When the walker
cannot advance even 0.1 m from that start because of intervening terrain, the
probe verifies the individual generated wall collider with a Rapier ray at its
measured height. A walker that advances toward or through a wall still must
pass the traversal check. The real AOI passes **135/135** Rapier probes: 29/29
walls, 90/90 fences, 14/14 quays and 2/2 stair traversals. **64** remaining
issues concern stair reconstruction and survey/ground evidence.

## Final measured state at the timebox

The final run on committed code again passes **135/135** Rapier probes but
reports **64** producer evidence issues, so the structures manifest remains
unaccepted and assembly correctly stops. One surveyed stair flight is built
and passes ascent/descent. The remaining issues are:

| Evidence gap | Count |
| --- | ---: |
| Stair landing independent lidar residual above 5 cm | 27 |
| Too few landing lidar samples | 11 |
| Measured rise below 5 cm | 8 |
| Isolated stair survey line | 5 |
| Ground contact missing at water/coverage gaps | 4 |
| Wall-top independent lidar residual above 10 cm | 4 |
| Bent/short stair survey line | 3 |
| Inconsistent flight width | 1 |
| Stair landing contact gap above 2 cm | 1 |

Sampling city stair landings up to 0.75 m beyond surveyed nosings, enlarging
sparse lidar search to 0.8 m, filtering the original sample toward class 2,
and using class 2 alone all left one accepted flight and 64 issues. Those
experiments were reverted; neither the 5 cm lidar nor 2 cm contact limit was
relaxed. Four OSM wall/fence/retaining-wall records lack dry ground contact;
four untagged OSM walls on near-level ground have no reliable lidar top.
The existing office lidar breakline reports contain curb and quay records only
(476 vector/ground records and 63 added lidar records); they provide no stair
landing or riser evidence to resolve these residuals.

The layer currently adds 22–55 visual meshes per L0 tile and 22–58 kB of
brotli-compressed GLB bytes per tile. Combined draw and 8 MB tile budgets,
street-level screenshots on WebGPU/WebGL2, and the shipped collision sweep
remain unverified because assembly requires an accepted structures manifest.

## Initial baseline

Measured 2026-10-26 in the `fix-structures-in-main` sandbox against the
checksum-verified shared ground layer. `make structures-build` produced 18
collision files, four visual files and 4,830 triangles: 90 fences, two walls,
one stair flight and 14 ground-classified quays. The real-data Rapier run passed
**71/107** cases (55 fences, two stair routes and 14 quays). It failed 35 fence
crossings and one wall crossing. The report has **273** blocking issues:

| Cause | Issues |
| --- | ---: |
| Ramp survey lines not covered by ground ramp triangles | 142 |
| Stair lidar/contact/grouping evidence | 55 |
| Wall top lidar evidence | 35 |
| Rapier crossings | 36 |
| Other survey evidence | 5 |

The 35 failed fence cases are short surveyed segments (median 0.30 m, maximum
0.65 m). The walker passes around them under Rapier's sliding collision. The
single failed wall route also passes. Extending these features without source
evidence would misplace geometry. The 142 ramp lines need ground surface
classification or an independently measured ramp footprint. The stair and wall
cases need new lidar-ground matching or survey grouping, retaining current
accuracy limits.

`make refresh` now depends on structures after ground. Assembly requires an
acceptance-passed structures manifest with collision and visual tiles. The
current run therefore stops before a published build. Street-level WebGPU and
WebGL2 screenshots, the collision sweep and tile budgets cannot be accepted
until these producer failures are resolved.

Reproduce from this worktree with its sandbox environment exported:

```sh
make -j2 structures-test
make structures-build ARGS="--area office-block --ground-layer $TURKU3D_WORK/build/layers/ground"
scripts/dev/gate --paths pipeline/turku3d/refresh.py pipeline/turku3d/package/assemble.py pipeline/turku3d/structures/layer.py pipeline/tests/test_refresh.py pipeline/tests/test_assemble.py
```

Detailed measured objects and probes are under
`$TURKU3D_WORK/reports/structures/`; the layer remains under
`$TURKU3D_WORK/build/layers/structures/` for diagnosis. No shared build
product was changed.
