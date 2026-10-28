# Incremental draft-world measurements

Measured on the M1 Max sandbox with real `t0_0_0` input and stored facade assets,
2026-10-28. A host build slot was held. Raw data was already available. These are
one-tile measurements, not an extrapolated all-Turku benchmark.

| Build | Wall seconds |
| --- | ---: |
| cold | 63.587 |
| warm | 0.779 |
| one stage changed | 0.807 |
| clean | 59.853 |

All 39 shipped mesh/collision/road/shared-texture payloads were byte-identical across
all four builds. Manifest timestamps and timings are metadata and excluded. The
one-stage change injects a different markings source identity while retaining equal
output: only markings reruns, and equal output prevents downstream invalidation.
Synthetic tests separately cover changed inputs, changed outputs, key types,
corrupt entries, artifact restoration and independent source dependencies.

Actual cache-operation elapsed times follow (seconds). These do not reuse cached
manifest timing fields. Collision rows are summed over the tile's sixteen cells.

| Stage | Cold | Warm | One stage changed |
| --- | ---: | ---: | ---: |
| terrain | 0.1378 | 0.0055 | 0.0046 |
| bridges | 0.0027 | 0.0022 | 0.0018 |
| buildings | 0.2200 | 0.0185 | 0.0180 |
| tunnels | 0.1897 | 0.0224 | 0.0214 |
| surface_boundaries | 2.4255 | 0.0087 | 0.0091 |
| ground_visual | 0.0297 | 0.0118 | 0.0109 |
| markings | 0.1336 | 0.0044 | 0.1313 |
| kerbs | 0.6954 | 0.0029 | 0.0029 |
| vegetation | 0.1047 | 0.0340 | 0.0337 |
| props | 0.0165 | 0.0033 | 0.0029 |
| building_visual | 0.2169 | 0.0194 | 0.0193 |
| fences | 0.1052 | 0.0024 | 0.0021 |
| package | 41.6346 | 0.0554 | 0.0090 |
| collision (16 cells) | 0.1445 | 0.0074 | 0.0069 |

Reproduce with the sandbox environment loaded:

```sh
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python -m turku3d.coarse.cache_benchmark --tile t0_0_0 --out "$TURKU3D_WORK/reports/incremental-tiles/recheck"
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_stage_cache.py pipeline/tests/test_coarse_latency.py
```

Live OCI first-to-full upgrades completed in 12.117s (`t0_-4_3`, 25 images) and
27.954s (`t0_3_-2`, 24 images). A missing-WFS supported tile (`t0_-6_-2`)
progressed from pending data to its structural draft in 5.078s. A real surveyed
bridge first-pass sanity run completed in 29.373s including slot admission.

Pages startup completed in 18.834s with no load or console errors, 449 textures
loaded, 50 collision cells, the player grounded on pavement at 4.473m and the car
supported on the road at 4.363m with all four wheel contacts. No published full-tile
requests were made. This check covers startup support, not an exhaustive driving
or bridge geometry audit. The bridge owner separately identified and is fixing a
first/full survey-height mismatch.

Complete previous full tiles now remain visible through producer revalidation;
replacement jobs skip the structural pass. Service-only changes no longer change
the generator identity. Unfinished requests survive deploys without an age cutoff.
New tiles still receive a structural first pass and a background textured upgrade.
