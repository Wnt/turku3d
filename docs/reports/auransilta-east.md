# Auransilta east and Tuomiokirkkosilta

Current OCI full-pass, non-stale COL payloads passed the production-car crossing check
in both directions on all seven named bridges (14/14). The test loads hash-verified
payloads through the production COL decoder/collider builder and uses the production
raycast car at approximately 6 m/s, starting 12 m before the mapped span and driving
10 m beyond it. There are no screenshots or browser drives.

Auransilta's east end previously terminated at H5.77 above a terrain depression:
the sampled drop was 2.07 m, beyond the t0_1_0/t0_1_1 seam. Nearest raw lidar
keys were higher than the rendered terrain TIN, so the old approach stopped too
soon. Both joins now sample the global ground lattice and require stable ground
beyond the join. The east extension grows from 6 m to 12 m; the west from 10 m
to 12 m. The repaired centre path has at most 3.7 cm between half-metre samples.

Tuomiokirkkosilta had the same short-join problem plus uncovered wedges between
its skew outline and rectangular extensions. Mapped continuation-road footprints
now fill those wedges. Its approximately 25.72 m OSM width agrees with the
inventory's 25.9 m useful width; the surveyed H7.02–7.36 deck profile is retained.
The concrete arch fascia was a filled web; it now has an open rib and vertical
spandrel posts, consistent with the [Yle photo](https://yle.fi/a/3-7387704).
Stone abutments and metal railings remain. The
[2025 inventory](https://turunraitiotie.fi/wp-content/uploads/2026/02/Turun-raitiotie_Turun-Tuomiokirkkosillan-vedenalainen-tarkkuusinventointi-1.pdf)
confirms a single opening. Detailed structural dimensions remain estimates.

OCI initially ran f515db1f (before bridge-geometry), and t0_1_0 was stale after a
failed refresh. Current source was deployed before baseline checks. The final
geometry was verified from source 4923cca39808; full/current manifests and hashed
payloads are retained locally under `$TURKU3D_WORK/reports/crossing-live-final/`.

| Bridge | Directions passed | Unsupported ticks |
| --- | --- | --- |
| Auransilta | 2/2 | 0 |
| Teatterisilta | 2/2 | 0 |
| Myllysilta | 2/2 | 0 |
| Martinsilta | 2/2 | 0 |
| Kirjastosilta | 2/2 | 0 |
| Tuomiokirkkosilta | 2/2 | 0 |
| Hämeensilta | 2/2 | 0 |

The route for Tuomiokirkkosilta follows a mapped carriageway, not the artificial
outline axis between its divided carriageways. Teatterisilta and Kirjastosilta
are tested for physical passage with the car, without implying legal car access.

The affected quick stage `coarse-bridge-crossing` includes 30 Python bridge cases
and a production-car synthetic seam/gap check (about 3 s combined). The negative
case rejects a two-metre exposed deck end. Real builds/downloads stay outside the
30-second quick budget. Frozen cached crossings are selected for the post-deploy run.

```sh
source ../sandbox.env
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 OPENBLAS_NUM_THREADS=1 bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_bridges.py
node --test tools/validator/test/bridge-crossing.test.ts
npm --prefix tools/validator run typecheck
node tools/validator/src/bridge-crossing.ts "$TURKU3D_WORK/reports/crossing-live-final"
node tools/validator/src/bridge-crossing.ts "$TURKU3D_WORK/reports/crossing-live-final" --cached
```
