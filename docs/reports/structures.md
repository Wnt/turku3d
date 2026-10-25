# W1-02 structures: integration and real acceptance

Status: **BLOCKED — full gate is red**, 2026-10-25.
Landing scope: structures code and synthetic acceptance.
The user deferred curbs and real AOI acceptance to the next iteration. Earlier
real acceptance below remains failing; it is not evidence of production readiness.
Branch: `wt/w1-02-structures`, rebased onto main `662f646`. Conflicts preserve
both coordination tooling and the macOS stop-race fix. No merge to main was made.
The main build has no `build/layers/ground/MANIFEST.json` at this checkpoint,
so no new real AOI build was run for this scoped landing.

## Changes

- Adopted Ruff formatting/checks and registered selective structures Python and
  Rapier coverage in `scripts/dev/gate.toml`.
- Replaced the provisional quay decoder with W1-01's actual `quays.json` contract.
  The build snapshots and verifies the producer's files without writing to that
  sandbox; mixed-generation inputs are rejected.
- Vectorized ground contact queries and added real-data Rapier probes over the
  supplied ground and generated structures. The probes use the game's shared
  collider builder, material friction, interaction groups and internal-edge flags.
  Barrier routes cross segment interiors, with a regression for two-point fences.
- Fixed a reproduced macOS task-stop race: an empty process group can transiently
  return EPERM after termination. The stop command checks live membership, while
  real permission failures remain errors. The original termination assertions and
  timeout limits remain unchanged.

## Reproduce

```sh
source ../sandbox.env
scripts/dev/gate
make -j2 structures-test
# Next iteration, once main publishes its plain ground layer:
make structures-build ARGS="--ground-layer $TURKU3D_DATA/build/layers/ground"
npm --prefix tools/validator run structures -- \
  "$TURKU3D_WORK/reports/structures/physics-cases.json" \
  "$TURKU3D_WORK/reports/structures/physics-runtime.json"
```

The historical real build and saved-case replay both exit nonzero because acceptance fails.
To repeat against the exact measured ground generation rather than the advancing
producer, pass the `ground_input.snapshot` path in the
[metrics JSON](structures-metrics.json) as `--ground-layer`.

## Historical ground input

Read-only source: `~/turku3d/wt/w1-01-ground/work/build/layers/ground/`.
The consistent snapshot contains **64 collision tiles and 868,620 triangles**.
Ground code hash: `4f39c844c7886fa2ac12a981654a324899f9894426e16783f2350d08c742e6c8`.
Manifest hash: `ec77d6ab3d3edb6e100184619107b7f8c1c2a9bd29e3ca2eef43cec97eaa0279`.
All copied artifact hashes matched and no source changed during the read. The
producer's `acceptance_passed` is **false**. That provenance is recorded separately
from the actual structures checks below.

## Historical real measurements

The build took **206.390 s** and emitted an explicitly blocked intermediate layer:
**49 collision tiles, 4 visual tiles and 18,058 triangles**. There are **223 fences,
3 walls, 3 OSM gap-fill stair flights and one 200.660 m quay**. No city-nosing flight
passed all reconstruction checks; the three emitted flights do not establish full
stair coverage. No ramp geometry passed coverage.

| Check | Measured result |
|---|---|
| Emitted stair landing error bounds | 2.465, 2.922, 4.963 cm; limit 5 cm |
| Emitted stair ground contact errors | 0.804, 1.427, 1.941 cm; limit 2 cm |
| Visual/collider stair endpoint difference | 0 cm by shared construction |
| Emitted wall top holdout errors | 1.620, 9.090, 1.250 cm; limit 10 cm |
| Fence height evidence | 3 class-1 stations; 1,735 default-height stations |
| Ramp coverage | 373 survey lines uncovered; input has no ramp-material triangles |
| Missing ground contacts | 8 within the supplied 500 m envelope; 139 in its acquisition margin |

There are **877 geometry/evidence issues**. Besides missing ramps and contacts,
these include 96 stair landing holdout errors above 5 cm, 42 wall-top holdout
errors above 10 cm, 14 stair contact gaps above 2 cm, sparse lidar and unresolved
survey grouping. The JSON retains the remaining counts and accepted measurements.
These are failures to resolve, not excluded samples or relaxed tolerances.

## Historical real physics

Replaying the saved cases through the runtime's collider builder took **61.51 s**.
**152 of 248 cases passed; 96 failed.** Three additional fence routes could not be
placed because their ground contacts were missing.

| Object/check | Pass / tested |
|---|---:|
| Stair ascent and descent | 6 / 6 (all three emitted flights) |
| Fence blocking | 129 / 220 |
| Wall blocking above 0.3 m | 1 / 2 |
| Quay character blocking | 17 / 20 stations |
| Quay 1,500 kg CCD proxy at 10 m/s | 16 / 20 stations |
| Quay stations passing both checks | 16 / 20 |

The quay failed at stations 4, 5, 6 and 15. The case/report IDs identify the exact
routes. These are route probes against the real neighbouring geometry; failed
barriers need topology, ground-contact and route inspection. A full raycast-vehicle
run remains an integration check; the proxy is not presented as that vehicle.

Full evidence stays in this sandbox:
`reports/structures/{metrics.json,physics-cases.json,physics-runtime.json,real-build.log,runtime-acceptance.log}`.
The committed metrics JSON includes the report/case hashes and snapshot location.
No shipped `.col` or tileset was produced.

## Remaining work

1. Resolve the rejected city flights and wall measurements against lidar and the
   ground contacts; do not substitute the three successful OSM flights for coverage.
2. Reconstruct the 373 ramp footprints from survey data or agree an explicit ramp
   mask with W1-01, then preserve those ground triangles with the ramp material.
3. Diagnose the 91 failed fence probes, the wall probe and four quay stations using
   the saved cases; resolve the eight internal contact holes and margin coverage.
4. Rerun complete real acceptance against the final plain ground generation before
   claiming real AOI coverage. Publish the validator stairs sidecar when integrating
   the assembled collision; the current landing establishes synthetic acceptance only.


## Final gate evidence

On main base `662f646`, `scripts/dev/gate --json` passed 19/20 stages in
293.930 s. The unchanged retry passed 18/20 stages in 383.638 s. Structures
synthetic acceptance passed in both runs (21 Python tests and 13 Rapier tests).
No assertion, timeout or threshold was weakened.

The retry failures were:

- `tooling-tests`: `test_rm_refuses_unmerged_and_live_then_releases`,
  `subprocess.TimeoutExpired: ... wt rm a ... timed out after 20 seconds`.
- `game-e2e`: WebGPU gamepad test exceeded 25,000 ms; WebGPU render-controls
  expected the time slider hidden after KeyH but it remained visible for 5,000 ms.
  The other 24 browser tests passed.

Logs: `$TURKU3D_WORK/reports/structures/final-gate.json` and `gate-retry.json`.
The iteration stops after this retry under the user timebox. Main advanced with
assembler integration during testing; closeout rebases those changes, but a
new gate run is required before landing. Historical real AOI failures are deferred
by scope and are not the reason for this blocked gate result.
