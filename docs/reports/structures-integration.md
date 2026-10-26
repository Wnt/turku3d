# Structures office integration: blocked acceptance

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
make structures-build ARGS="--area office-block --ground-layer $TURKU3D_DATA/build/layers/ground"
scripts/dev/gate --paths pipeline/turku3d/refresh.py pipeline/turku3d/package/assemble.py pipeline/turku3d/structures/layer.py pipeline/tests/test_refresh.py pipeline/tests/test_assemble.py
```

Detailed measured objects and probes are under
`$TURKU3D_WORK/reports/structures/`; the layer remains under
`$TURKU3D_WORK/build/layers/structures/` for diagnosis. No shared build
product was changed.
