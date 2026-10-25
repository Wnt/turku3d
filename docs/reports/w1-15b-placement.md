# W1-15b placement QA

The instanced producers now share `turku3d.placement`: exact published collision
support, semantic surfaces, bridge-deck availability, whole-base access/building/object
clearance, and nearby walkway/road orientation. Valid candidates are height-snapped;
small offsets are repaired on a deterministic grid up to 2 m. Every source violation
and omission is retained in JSON and an embedded-orthophoto HTML report. Unresolved
hard violations fail publication. Signs use the props producer.

Two assembler bugs also belonged to this task: batching discarded existing tree GPU
instances, and accumulated transforms on shared prop primitives. GPU batches are now
preserved, and each ordinary instance bakes its own primitive clone. Tests check
packaged tree instance counts/bounds and the exact world bounds of two shared benches.

## Local evidence

Artifacts are under `/Users/wnt/turku3d/wt/w1-15b-placement/work/reports/placement/`.
The baseline is a read-only copy of the shared published props, vegetation and parked
layers; the rebuilt ground/building context is copied into this sandbox. No main files
were changed or deployed. Before/after screenshots are actual GLB layer previews with
identical cameras, not claims of textured live-game acceptance.

- `props/index.html`, `vegetation/index.html`, `traffic_static/index.html`: maps/rows.
- `validation.json`: combined published-inventory audit, including orientation unknowns.
- `aurasilta-before.png`, `aurasilta-after.png`: Auransilta vicinity (city register name).
- `rantakatu-before.png`, `rantakatu-after.png`: Läntinen Rantakatu 15 riverside.
- `teatterisilta-before.png`, `teatterisilta-after.png`: the three registered bridge benches.
- `gate.json`, `props-build.log`, `vegetation-build.log`, `parked-build.log`, `assemble.log`.

Measured output: 756 props, 248 trees and 16 parked cars. The source audit omitted
545 props and 86 trees. Not all omissions indicate misplaced live assets: the props
request includes the wider Phase 1 margin, where the published ground has no support.
317 props, 134 trees and one car were relocated, all less than 2 m. Height-only changes
are counted separately in the JSON transforms. Source props/cars start with an
unassigned zero height; their source `buried` audit label is not a live-build measurement.

The missing-deck inventory contains 16 props and one lidar tree candidate. The three
bench IDs `GIS_Varusteet:314386465`, `GIS_Varusteet:314386532` and
`GIS_Varusteet:314386536` are around engine x2–9/z390–399, at **Teatterisilta**, rather
than Auransilta. Both requested camera locations are retained; the additional bridge
view shows the specific omitted benches. Ground polygons labelled material 24 cannot
stand in for actual structures-layer bridge decks.

## Verification results

Combined inventory audit: **zero hard violations** across 756 props, 248 trees and
16 cars; 55 prop orientations remain explicitly unverified. Focused synthetic tests:
**40 passed in 11.87 s** with two workers. Final selective gate: **GREEN, 37.441 s**
under concurrent host load; no assertions or thresholds were weakened. The gate uses
conservative full pipeline coverage for the new shared module.

## Exact reproduction and local redeploy

```sh
cd /Users/wnt/turku3d/wt/w1-15b-placement/repo
source ../sandbox.env
bash pipeline/env/run.sh python -m turku3d.props --area phase1
bash pipeline/env/run.sh python -m turku3d.vegetation
bash pipeline/env/run.sh python -m turku3d.parked --seed 16
bash pipeline/env/run.sh python -m turku3d.placement
bash pipeline/env/run.sh python -m turku3d.placement.preview
bash pipeline/env/run.sh python -m pytest -q -n 2 pipeline/tests/test_placement.py pipeline/tests/test_props.py pipeline/tests/test_vegetation.py pipeline/tests/test_parked.py
scripts/dev/gate --base main
make tile-build T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
make tile-show
```

`make tile-show` rebuilds the game and serves the sandbox on port 5541. Ground/building
manifests must exist before the producers run. Preview generation also requires the
baseline copies under `reports/placement/before/`; it uses sandbox port 5543 and an
isolated browser profile. No process in another sandbox is used or stopped.

## Remaining concrete work

Model Auransilta, Teatterisilta and the other mapped bridge decks as structures, then
rerun omitted instances. Complete mapped tree-pit/access/path evidence where the
cached sources omit it. The combined audit explicitly lists orientation that cannot be
verified within 12 m or at coincident path positions. Clearance is against mapped
geometry and object bases; canopy overlap and tilted vehicle suspension are not fitted.
