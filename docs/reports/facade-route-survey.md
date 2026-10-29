# Route-ordered facade survey

The first published ring covers 0–150 m of walking route distance from the
pizzeria at engine (77.1, 187.6). Its 62 candidate walls have 12 accepted readings
and 50 explicit rejections. Rejections include missing cached whole-wall views,
foliage, construction hoarding, unresolved mixed facade sections and a camera
pose that could not be corrected safely. They retain their earlier appearance;
this is completed classification, not a claim that every wall is photographed.

The frozen navigation snapshot contains 153 delivery destinations, including all
12 landmarks. The cached LOD2 inventory contributes 7,254 candidates to the
corridor: primary/secondary/tertiary streets, foot/car delivery routes and stored
facade buildings. Rings are 150 m wide, sorted by route distance. The shared
`pipeline/turku3d/routes_rings.py` computes the route ordering for other consumers.
The initial inventory matches 237 of 347 stored building identities; unresolved
identities remain listed in the durable queue summary for subsequent expansion.

## Reading and preservation

Panoramas are fetched from the existing OCI cache only. Line endpoints in a
preview constrain a bounded camera rotation; final wall crops use that refined
rotation. Sparse or inconsistent evidence retains the supplied pose and is
reviewed explicitly. The raw pose, fitted correction, source hash, crop hash,
forms, rejected views and timing remain outside git. Only this run's temporary
full panorama copies are deleted after cropping; original sources are untouched.

The fixed form records rows bottom-to-top, bay kinds left-to-right, head shapes,
surrounds, material, ornament level, cornice, floor-boundary string courses,
balconies, occlusions and field confidence. Equal-row/bay layout priors turn
counts into openings on measured wall extents. Opening sizes are not photographic
measurements. Conflicting counts do not change register storeys or shell height.
Unknown bays retain prior treatment. The painter supports frame, pediment and
rusticated surrounds; detailed sculptural ornament is recorded but not recreated.
Existing matching pilot rectangles take priority over the new count-only layout.
The modern `concrete_panel` descriptor now resolves to the concrete atlas material.

Existing hand-painted/generated maps, AI descriptions, facade-kit outputs and
source images are preserved in OCI `/srv/turku3d/data/facade-assets`,
`facade-approvals`, `facade-params` and `textures`, and in the git texture library.
This slice does not change the precedence selector or rewrite those assets.
Reviewed programs apply only to matching walls at paint time; disabling a
building's reviewed program (`needs_review: true`) retains the stored asset path.

## Durable evidence and initial timing

Local root: `~/turku3d/data/facade-characterization/`. `inputs/` freezes navigation,
OSM tertiary geometry, panorama metadata, existing asset metadata and LOD2 wall
inventory. `queue.json` is immutable; `events.jsonl` appends state transitions.
`batches/*` holds original and alternate crops, contact sheets, raw answers,
validated programs and quota/timing records. `publications/ring00/` contains the
resolved queue snapshot and programs, including the superseding rejection of a
mixed-section wall. Corrections never erase the initial reading evidence.
Validated programs and queue snapshots are also copied to OCI
`/srv/turku3d/data/facade-characterization/`; reference photographs stay local.

The initial ring used 34 crop views, including alternates. Preparation through
publication snapshot took 780 seconds, 12.6 seconds per candidate wall. This
includes form/compiler work and waiting, not just visual reading. Per-batch elapsed
and preprocessing timings are retained separately. The account's integer weekly
quota reading rose from 33% to 34%; this is account-wide, with 1-point resolution.
New readings stop at 82%.

## Reproduction

```sh
source ../sandbox.env
ROOT="$TURKU3D_DATA/facade-characterization"
PYTHONPATH=pipeline:textures bash pipeline/env/run.sh python \
  tools/research/facade_characterization/routes.py prepare \
  --root "$ROOT" --batch NEW-UNUSED-BATCH --count 16
# Read its contact.jpg and write answers.json using the fixed structure form.
PYTHONPATH=pipeline:textures bash pipeline/env/run.sh python \
  tools/research/facade_characterization/routes.py compile \
  --root "$ROOT" --batch NEW-UNUSED-BATCH \
  --publish textures/library/facade-programs.json
PYTHONPATH=pipeline:textures PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 OPENBLAS_NUM_THREADS=1 \
  bash pipeline/env/run.sh python -m pytest -q \
  pipeline/tests/test_facade_structure.py pipeline/tests/test_facade_programs.py
```

New batch names prevent accidental replacement of evidence. An alternate uses
`--alternate-from BATCH --wall-ids ID ...`; source selection excludes the first
panorama and camera positions within four metres of it. Per-building program
hashes preserve selective appearance invalidation; no area rebuild is required.
