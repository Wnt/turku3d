# Published collision validation follow-up

Branch `wt/fix-collision-validate`. Inputs: the sandbox's four-tile live build
(64 collision tiles plus halo). No shared data was changed.

| Check | Published report before | With this branch |
|---|---|---|
| Cars | 16/16 tiles fail (t0_0_0) | Known red, reported outside the release gate: 12/16 t0_0_0 tiles fail. Four 30 m/s wall/building crossings and 504 curb-response runs remain. |
| Buildings | 4/16 tiles fail (t0_0_0) | 0/64 fail with the release settings (128 walkers, 2 s) and 0/16 for each visual tile alone. |
| Curbs | 1/16 tiles fail (t0_0_0) | 96.7% of 1,739 stations succeed over the four tiles, median declared step error 0.1 cm; 90.1–99.0% per visual tile. |
| Stairs | No published structures layer | No testable input; unchanged. |

## Buildings

Validator causes on t0_0_0: the physics halo stopped at the requested visual
tile while building shells crossed that boundary; walkers standing or falling
above roofs counted as inside footprints; and a small footprint entirely
enclosed by neighboring footprints had no exterior start. Rapier 0.21 returned
`featureId=0` for tested trimesh ray hits, so decoded roof triangles identify
roof height.

The first four-tile precheck then failed 3/64 tiles (`c_1_6`, `c_2_5`, `c_3_4`).
All three were restaurant boats on the Aura: walkers started on the synthetic
river bed 3 m below the water sensor and walked under the floating hull. Walkers
now start only on dry ground, submerged positions count as swimming, and a
footprint with no dry start 0.5–3 m outside its edges is listed as enclosed.
When 30 random starts miss a short dry stretch, the
scanned dry start is used, so the footprint is still tested (the boat in `c_1_6`, reachable only from a bridge deck).

A 2,000-walker run then found one content defect: the point-only placeholder
`gis-103447041A` (`c_5_6`) straddles a 9 m terrace edge. Its base came from the
nearest ground returns on the upper terrace (29.2 m), so walkers on the lower
street (20.8 m) passed under it. Placeholders now span the class-2 returns under
the square: base at the lowest, roof the placeholder height above the highest.

## Curbs

The `c_1_2` mesh-face failure was a real 12 cm step with a pole immediately
behind it: the walker climbed and stopped before the old 0.6 m travel threshold.
A walker reaching the upper surface 2 cm past the face now counts as climbed.

On the four-tile scope, coverage was 74.9% (43% in t0_0_1). Two causes, both
measurement, not mesh: the shipped mesh reproduces the breakline side heights
within millimetres.

- The validator took the step as the highest point 0.05–0.45 m behind the line
  minus the ground 0.6 m in front. On cross-fall and ramps (up to 70% slope
  behind curbs in t0_1_1) that added up to 46 cm. Both sides are now fitted
  with a repeated-median line through 0.1–0.45 m and evaluated at the curb line,
  the same way the declared step is fitted from lidar profiles.
- The ground sidecar declared the mean of absolute end steps. For 101 segments
  the high side swaps between the ends, so the interpolated mesh has almost no
  step at the station; the sidecar now declares the signed mean, which drops 83
  of them below the 3 cm flush threshold.

The AOI uses the Phase 1 85% coverage and 5 cm median step-error limits; local
failures stay in the per-tile report.

## Cars

Car probes exclude rooftop starts, obstructed chassis approaches and curb
midpoints whose step differs across the two wheel tracks. High-speed barriers
come from actual vertical `.col` faces. The synthetic 7/12/15 cm by 3/6/10 m/s
acceptance still passes all nine scenarios. Published collision remains red:

- `c_2_1:wall` near (170.22, 104.88)
- `c_2_2:building` near (154.64, 154.18)
- `c_1_4:building` near (130.29, 242.76), tested from `c_2_3`
- `c_3_3:building` near (204.89, 187.61)

The remaining curb-response failures include missing strikes, speed loss
outside 5–20% and insufficient front-axle travel; coordinates, speeds and
outcomes are in the release's `reports/validate-cars.json`. The live build gates
format, seams, buildings and curbs on affected collision tiles and runs the car
assertions separately as a known-red report. A validator load error or missing
car report still fails the live build. The normal `make tile-validate` command
still requires cars.

Reproduce the release gate on a sandbox build:

```sh
make tile-validate T="t0_0_0 t0_0_1 t0_1_0 t0_1_1" TILE_WALKERS=128 \
  VALIDATOR_REQUIRE=buildings,curbs \
  VALIDATOR_ARGS="--checks format,seams,buildings,curbs --walk-seconds 2"
```

The stronger building run used `--walkers 2000 --walk-seconds 4 --checks buildings`.
