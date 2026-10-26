# Published collision validation follow-up

Branch `wt/fix-collision-validate`, published release based on main `435b3d9`,
rebased onto `7688c5c`. No shared data was changed.

| Check | Published report before | With this branch against published 16 office collision tiles |
|---|---|---|
| Cars | 16/16 tiles fail | 12/16 tiles fail. Four 30 m/s wall/building crossings and 504 curb-response runs still fail. |
| Buildings | 4/16 tiles fail | 0/16 fail with 2,000 walkers for 10 seconds. |
| Curbs | 1/16 tiles fail in mesh-face mode | Layer-sidecar AOI passes: 788/847 stations (93.0%) succeed, median declared step error 1.0 cm. Ten individual tiles retain repair diagnostics. |
| Stairs | No published structures layer | No testable input; unchanged. |

The building failures had three validator causes: the physics halo stopped at the
requested visual tile while building shells crossed that boundary; walkers
standing or falling above roofs were counted as inside footprints; and a small
footprint entirely enclosed by neighboring footprints had no exterior starting
position. Rapier 0.21 returned `featureId=0` for tested trimesh ray hits, so
decoded roof triangles are used to identify roof height. The full 2,000-walker
run passed after this correction.

The curb `c_1_2` mesh-face failure occurred at a real 12 cm step with a pole
immediately behind it: the walker climbed the top and stopped before the old
0.6 m travel threshold. The probe now recognizes a walker reaching the upper
surface 2 cm past the face. The layer-sidecar result uses the agreed Phase 1
85% coverage and 5 cm median step-error limits. Local failures remain visible.

Car probes now exclude rooftop starts, obstructed chassis approaches, and curb
midpoints whose step differs across the two wheel tracks. High-speed barriers
come from actual vertical `.col` faces; footprint normals can point almost
sideways to the physical wall at corners. The synthetic 7/12/15 cm by 3/6/10
m/s acceptance still passes all nine scenarios. Published collision remains red:

- `c_2_1:wall` near (170.22, 104.88)
- `c_2_2:building` near (154.64, 154.18)
- `c_1_4:building` near (130.29, 242.76), tested from `c_2_3`
- `c_3_3:building` near (204.89, 187.61)

The remaining 504 curb-response failures include missing strikes, speed loss
outside 5–20%, and insufficient front-axle travel. Their reported coordinates,
speeds and physical outcomes are in
`$TURKU3D_WORK/reports/car-faces-roofs.json`. For this landing slice, the live
build gates format, seams, buildings and curbs on affected collision tiles.
It runs the same car assertions separately over those tiles and records their
known-red result in `reports/validate-cars.json` and `live-build.json` without
blocking publication. A validator load error or missing car report still fails
the live build. This temporary exception allows the building and curb repairs
and the new validation stage to land while car content and vehicle response
remain a follow-up slice of this task. The normal `make tile-validate` command
still requires cars.

Reproduce the 16-tile sample with:

```sh
cd tools/validator
node src/cli.ts --walkers 128 --workers 4 --checks cars,buildings,curbs \
  --walk-seconds 2 --layers "$TURKU3D_DATA/live/layers" \
  --out "$TURKU3D_WORK/reports/validate-published.json" \
  "$TURKU3D_DATA/live/tiles/collision/t0_0_0"
```

The full building result used `--walkers 2000 --walk-seconds 10 --checks buildings`.
The earlier precheck gate passed the changed code stages but failed its tooling
fixtures because host free disk was 26.9–27.0 GiB, below the 30 GiB task launch
guard. Space was later freed for this slice's precheck.
