# Route-ordered facade survey

Latest coverage: complete candidate classification through 900 m route distance;
see the sixth publication below. Original publication counts remain historical.

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
The initial inventory matched 237 of 347 stored building identities. A subsequent
binding-based supplement resolved all 110 missing identities and added 391 walls;
all 347 stored identities now have queue entries (7,645 candidates in total).

## Reading and preservation

Panoramas are fetched from the existing OCI cache only. Line endpoints in a
preview constrain a bounded camera rotation; final wall crops use that refined
rotation. Sparse or inconsistent evidence retains the supplied pose and is
reviewed explicitly. The raw pose, fitted correction, source hash, crop hash,
forms, rejected views and timing remain outside git. Only this run's temporary
full panorama copies are deleted after cropping; original sources are untouched.

The fixed form records rows bottom-to-top, bay kinds left-to-right, head shapes,
surrounds, material, ornament level, cornice, floor-boundary string courses,
balconies, occlusions and field confidence. Qualitative row heights, bay units and paired-opening counts turn
counts into openings on measured wall extents. Continuous ribbon glazing can keep
its pane count unknown. Floor material bands and pointed heads are supported. Opening sizes are not photographic
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
panorama and same-date camera positions within four metres of it. Historical
views at the same position remain useful for temporary occlusions. Per-building program
hashes preserve selective appearance invalidation; no area rebuild is required.

## Second publication: complete classification through 300 m

| Route-distance ring | Candidates | Characterized | Rejected | Rendering deferred |
| --- | ---: | ---: | ---: | ---: |
| 0–150 m | 88 | 18 | 70 | 1 |
| 150–300 m | 140 | 39 | 101 | 21 |

The 57 characterized walls include 35 eligible for the current painter and 22
with structured observations retained for later compilation. Deferral is separate
from rejection: mixed stair strips, clock towers, blind niches, permanent parking
screens, partial views and uneven storefront sections retain useful descriptive
evidence without replacing the original art with an unsuitable repeated layout.
Confidence and unknown counts remain explicit. Earlier mixed-section and clock
rejections were enriched through append-only revisions.

Paired windows, pointed portal heads, qualitative short rows, unequal bay units,
continuous ribbons and per-floor material bands extend the form/compiler. A
single observed ground strip does not replace upper-floor treatment. Original
precise pilot rectangles remain preferred. The second durable publication is
`publications/ring01/`; original and revision forms remain in their batch folders.
Weekly account quota is 38% (33% at start). Batch elapsed time includes alternate
view preparation, schema work and deployment interruptions; it must not be read
as pure visual annotation time. Complete timings are in each `finished.json`.

## Third publication: complete classification through 450 m

| Route-distance ring | Candidates | Characterized | Rejected | Painter eligible | Rendering deferred |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0–150 m | 88 | 26 | 62 | 17 | 9 |
| 150–300 m | 140 | 46 | 94 | 18 | 28 |
| 300–450 m | 187 | 69 | 118 | 33 | 36 |

All 415 candidates are classified: 141 characterized, 274 rejected. New structured
observations distinguish French windows with Juliet rails, recessed loggias,
projecting bay windows, curtain walls, opaque blind niches, frieze vents and
reflections. Counts of panes, floors or bays stay unknown where foliage or mixed
planes prevents a reliable reading. Several stored waterfront bindings have no
visible facade in either view; their source assets and bindings remain untouched.

A systematic bad-crop cause was found: when local ground points could not support
a plane, Look Around's uncalibrated provider elevation placed some cameras around
20 m too high. The fallback now uses the explicit facade-base plus assumed camera
height approximation; calibrated N2000 ground remains preferred and original
provider metadata is preserved. All 171 initial inner-ring rejections were
revisited. This recovered clear architectural views as well as useful partial
observations. Earlier answers and corrections remain append-only. One balcony
row count was corrected after a full-resolution reread, excluding the roof slab.

`publications/ring02/` contains the complete queue state, programs and timing.
Elapsed time from the first batch is 7,542.6 seconds (18.2 s/candidate), including
schema work, repeated crops, deployment and waits. Weekly account quota is 40%,
up seven integer points from the measured 33% start; new readings stop at 82%.
The durable local footprint is approximately 113 MB; fetched full panoramas are
removed after cropping, original sources are never touched.

The previous Pages/OCI source activated successfully, but a final attempt to
disable the already removed legacy prebuild unit caused a diagnostic error.
Legacy service cleanup now checks its unit file before stopping/disabling it.
Independent curl smoke passed in 0.276 s and frozen-source package/render tests
passed in 560.4 s including host-slot wait. The third publication runs the affected
height, form and deployment checks, followed by selective streetview geometry and
program compatibility checks.

## Fourth publication: complete classification through 600 m

| Route-distance ring | Candidates | Characterized | Rejected | Painter eligible | Rendering deferred |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0–150 m | 88 | 26 | 62 | 17 | 9 |
| 150–300 m | 140 | 46 | 94 | 18 | 28 |
| 300–450 m | 187 | 69 | 118 | 33 | 36 |
| 450–600 m | 343 | 88 | 255 | 22 | 66 |

All 758 candidates through 600 m are classified: 229 characterized (90 painter
eligible, 139 deferred) and 529 explicitly rejected. Balcony stacks, Juliet rails,
blind timber arches, courtyard stairs, conservatories, clipped stored returns and
asymmetric storefronts retain detailed section descriptions and confidence.
Original maps and descriptions remain available while unsupported compositions
continue using their original artwork.

The form adds categorical small, regular, wide and tall openings, including
per-bay lists. These preserve small service windows between larger living-room
windows using layout priors, without hand-entered image coordinates. Pose fitting
now accepts consistent corrections up to six degrees per axis, with residual and
combined-rotation limits; one timber frontage improved from 5.15° to 0.78° median
line error. Confirmed indoor panoramas are excluded from subsequent exterior
selection using an append-only source list. A low Google camera elevation proved
to be an underground garage, so the experimental Google height change was
reverted; only the previously published Look Around fallback remains changed.

An unavailable alternate view now retains an accepted primary observation. The
state audit restored two such records; all previous evidence remains intact.
`publications/ring03/summary-v2.json` corrects the first snapshot's eligible tally
for older events that predate the explicit rendering-status field. Programs,
queue state and source exclusions are mirrored to OCI; photographs remain local.

Elapsed time from the first batch is 9,797.6 seconds, 12.9 s/candidate including
preparation, schema changes, retries, deployments and waits. Weekly account quota
is 43%, ten points above the measured start. The local evidence footprint is
176 MB. Reading continues outward with the same 82% stopping threshold.

## Fifth publication: complete classification through 750 m

| Route-distance ring | Candidates | Characterized | Rejected | Painter eligible | Rendering deferred |
| --- | ---: | ---: | ---: | ---: | ---: |
| 600–750 m | 291 | 108 | 183 | 30 | 78 |
| Cumulative 0–750 m | 1,049 | 337 | 712 | 120 | 217 |

This publication adds readings and restores the shared program contract for deferred and ground-only records. Readings distinguish ventilation louvres from
glazing, blind masonry arches from portals, reflected windows from actual
openings, offset stair windows, isolated window stacks, repeated balcony rows,
classical fanlights and arched vehicle doors. One reused-crop revision corrects
an opening-size label: narrow upright windows occupy a normal fraction of their
storey, so they use the regular-height prior rather than the tall-opening prior.
Original answers remain available beside the correction.

`publications/ring04/` freezes the programs, complete queue state and source
exclusions. The new ring took 4,548 seconds between publication snapshots,
15.6 s/candidate; cumulative elapsed time is 14,345.6 seconds, 13.7 s/candidate.
These wall-clock figures include preparation, code/record checks, waiting and
publication work, not just annotation. Per-batch timings and quota readings are
retained. Weekly account quota is 46%, thirteen integer points above the measured
33% start. Reading continues at 750 m, with new reading stopping at 82%.

The previous slice passed selected checks in 16.745 s, Pages package/upload in
10.848 s, OCI activation in 19.811 s, curl smoke in 0.263 s and frozen-source
facade asset/kit tests in 5.762 s. This data-only slice selects the actual-library
review-boundary checks before publishing, followed by broader facade program and
structure compatibility checks.

The actual-library pre-merge check exposed omitted empty opening sections on
deferred records. These now explicitly contain no window/base rectangles.
Ground-only glazing now uses `base.openings_m` with no upper-window rectangles or
repeated middle storeys; per-opening head and surround shapes remain intact in
the painter. Tests retain their assertions and additionally check preserved upper
facades and ground opening shapes. `publications/ring04-contract/` supersedes the
original program snapshot; `migrations/20261029-program-contract/` preserves the
before/after records and changed-wall list. Original raw forms are unchanged.

## Sixth publication: complete classification through 900 m

| Route-distance ring | Candidates | Characterized | Rejected | Painter eligible | Rendering deferred |
| --- | ---: | ---: | ---: | ---: | ---: |
| 750–900 m | 380 | 115 | 265 | 51 | 64 |
| Cumulative 0–900 m | 1,429 | 452 | 977 | 171 | 281 |

The new ring distinguishes offset stair lights, enclosed balcony stacks,
glass-block stairs, recessed loggias, colonnades, blind niches, decorative
medallions, opaque timber gates and curved masonry lintels above flat glass.
Readable partial architecture remains structured even when foliage prevents full
counts. Unusable views and active renovation wraps have explicit rejections.
Alternate views recovered several obscured walls and corrected a narrow-window
size prior; neither raw answers nor original artwork were overwritten.

All 380 candidates in this ring are classified. The append-only snapshot is
`publications/ring05/`, with programs, queue state, source exclusions and timing
mirrored to OCI. Original photographs, rectified crops and contact sheets remain
local. The ring adds 51 painter-eligible records and 64 descriptive records that
retain prior rendering pending richer geometry or better visibility.

Elapsed time from the first batch is 17,779.8 s, or 12.4 s/candidate. The interval
since the previous publication is 3,434.2 s for 380 candidates (9.0 s/candidate),
including preparation, alternate views, corrections and waits. Integer account
quota is 49%, versus 33% at the measured start and 46% at the preceding snapshot.
The 82% new-reading cutoff remains in force; work continues with the next ring.

The actual-library gate encountered the first four buildings with missing/zero
register storey counts. Their five walls are descriptive/deferred. The library
check now verifies the complete established storey provenance contract: exact
register value, register source/support 1 for known counts, unavailable source
and support 0 for missing counts. No count is inferred from photographs, and no
program data was changed to satisfy the check.
