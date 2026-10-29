# Route-ordered facade survey

Latest coverage: complete candidate classification through 2,250 m route distance;
see the fourteenth publication below. Original publication counts remain historical.

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

## Seventh publication: complete classification through 1,050 m

| Route-distance ring | Candidates | Characterized | Rejected | Painter eligible | Rendering deferred |
| --- | ---: | ---: | ---: | ---: | ---: |
| 900–1,050 m | 345 | 133 | 212 | 35 | 98 |
| Cumulative 0–1,050 m | 1,774 | 585 | 1,189 | 206 | 379 |

This ring records timber lintels and attic vents, garage shutters, drive-through
openings, offset stair glazing, broad balcony groups, service slots and blank
end walls. Alternate views recover details from tree-obstructed fronts. Tiny
vents remain explicitly distinct from ordinary windows; incomplete or mixed
structures retain descriptive observations and their earlier appearance.

The immutable `publications/ring06/` snapshot contains programs, queue state and
timing, mirrored to OCI with the raw forms and append-only logs. Original
photographs, crops and contact sheets remain local; stored artwork is unchanged.
All 345 candidates are classified, with 35 painter-eligible and 98 deferred
readings in the new ring. Rejections include unavailable cached viewpoints and
unreadable foliage, fences or other obstructions.

Elapsed time is 22,202.1 s, or 12.5 s/candidate. This ring interval is 4,422.3 s
for 345 candidates (12.8 s/candidate), including preparation, alternates,
validation, publication and interruptions. Weekly account quota is 54%, versus
33% at the measured start and 49% at the preceding publication. Continue outward
in route-distance order; new readings stop at 82%.

## Eighth publication: complete classification through 1,200 m

| Route-distance ring | Candidates | Characterized | Rejected | Painter eligible | Rendering deferred |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1,050–1,200 m | 444 | 113 | 331 | 21 | 92 |
| Cumulative 0–1,200 m | 2,218 | 698 | 1,520 | 227 | 471 |

The ring includes the railway station glass-block entrance and clock, historic
projecting bays and pediments, timber attic vents, industrial glazed garage doors,
Juliet rails, offset stair windows and narrow service slots. New alternate views
recover a previously scaffold-covered wall and correct incomplete window counts.
Mixed planes, tiny features and unreadable bands retain structured descriptions
without replacing their stored appearance. Fully hidden or unidentifiable walls
remain explicitly rejected; no openings are inferred through vegetation.

`publications/ring07/` freezes validated programs, full queue state and timing.
Programs, forms and append-only metadata are mirrored to OCI. Photographs and
straightened crops remain local, and original maps, AI descriptions and facade-kit
outputs remain untouched. The new ring adds 21 painter-eligible records and 92
descriptive records; all 444 candidates have a terminal classification.

Elapsed time from the first batch is 24,501.5 s, or 11.0 s/candidate. This ring
interval is 2,299.4 s for 444 candidates (5.2 s/candidate), including preparation,
alternates, validation and publication work. Per-batch timings remain available;
these are not pure annotation speeds. Weekly account quota is 57%, versus 33%
at the measured start and 54% at the previous publication. Continue outward;
new reading stops at 82%.

## Ninth publication: through 1,350 m

All 369 candidates in the 1,200–1,350 m ring are classified: 70 characterized
(5 painter eligible, 65 descriptive/deferred) and 299 rejected. Dense hedges
and trees conceal many low facades and stored garden structures in this ring.
Alternate views recovered timber siding, porch and window details where possible.
The usable programs add historic timber window rhythms and clear apartment end-wall
window stacks. Mixed balcony planes, shop ribbons, mesh screens and opaque service
panels remain descriptive; their existing artwork is preserved.

A saved revision corrects one earlier descriptive balcony count from eleven to ten
visible glazed levels. The original answer and source evidence remain intact.

| Route ring (m) | Candidates | Characterized | Rejected | Painter eligible | Deferred |
|---|---:|---:|---:|---:|---:|
| 0–150 | 88 | 26 | 62 | 17 | 9 |
| 150–300 | 140 | 46 | 94 | 18 | 28 |
| 300–450 | 187 | 69 | 118 | 33 | 36 |
| 450–600 | 343 | 88 | 255 | 22 | 66 |
| 600–750 | 291 | 108 | 183 | 30 | 78 |
| 750–900 | 380 | 115 | 265 | 51 | 64 |
| 900–1050 | 345 | 133 | 212 | 35 | 98 |
| 1050–1200 | 444 | 113 | 331 | 21 | 92 |
| 1200–1350 | 369 | 70 | 299 | 5 | 65 |
| Total | 2587 | 768 | 1819 | 232 | 536 |

The immutable `publications/ring08/` snapshot and all append-only metadata/programs
are mirrored to OCI. Elapsed time is 26,322.8 seconds,
10.2 seconds per classified candidate. The latest publication
interval is 1,821.3 seconds / 369 candidates = 4.9 seconds per candidate,
including preparation, alternate reads, checks and waits. Per-view batch timing remains
in each `finished.json`; these candidate averages include unavailable-view rejections.
Weekly account quota is 58%, up 25 points from the measured 33% start. Reading
continues outward with the 82% stop threshold.

## Tenth publication: through 1,500 m

The 1,350–1,500 m ring adds 14 characterized walls (6 painter eligible,
8 descriptive/deferred) and 265 explicit rejections among 279 candidates.
Available clear views include two ochre timber frontages, single-column apartment
end walls, a nine-window retail upper row and eight continuous glazing bands.
An alternate recovers a curved balcony and window composition hidden in the
primary image. Most other candidates lack usable cached views or remain behind
dense foliage after an alternate. Existing facade art remains intact.

| Route ring (m) | Candidates | Characterized | Rejected | Painter eligible | Deferred |
|---|---:|---:|---:|---:|---:|
| 0–150 | 88 | 26 | 62 | 17 | 9 |
| 150–300 | 140 | 46 | 94 | 18 | 28 |
| 300–450 | 187 | 69 | 118 | 33 | 36 |
| 450–600 | 343 | 88 | 255 | 22 | 66 |
| 600–750 | 291 | 108 | 183 | 30 | 78 |
| 750–900 | 380 | 115 | 265 | 51 | 64 |
| 900–1050 | 345 | 133 | 212 | 35 | 98 |
| 1050–1200 | 444 | 113 | 331 | 21 | 92 |
| 1200–1350 | 369 | 70 | 299 | 5 | 65 |
| 1350–1500 | 279 | 14 | 265 | 6 | 8 |
| Total | 2866 | 782 | 2084 | 238 | 544 |

Immutable `publications/ring09/` and append-only metadata/programs are mirrored
to OCI. Elapsed time: 27,116.6 seconds, 9.5 seconds per
classified candidate. Latest publication interval: 793.8 seconds / 279
candidates = 2.8 seconds per candidate. These include preparation, unavailable-view
rejections and publication; per-view batch timing is retained separately. Weekly
account quota is 59% (26 points above the measured 33% start); reading continues
with the 82% stop threshold.

## Eleventh publication: through 1,650 m

The 1,500–1,650 m ring adds 22 characterized walls (1 painter eligible,
21 descriptive/deferred) and 332 rejections among 354 candidates. The usable
program records seven rows with six alternating-width window groups. Detailed
descriptions preserve solar-shading screens, open parking decks, offset glass-block
inserts, crenellated stone facades, projecting balconies and asymmetric entrances.
Alternate views resolve some occlusions; uncertain target-plane associations stay
explicitly deferred. Existing facade art is preserved.

| Route ring (m) | Candidates | Characterized | Rejected | Painter eligible | Deferred |
|---|---:|---:|---:|---:|---:|
| 0–150 | 88 | 26 | 62 | 17 | 9 |
| 150–300 | 140 | 46 | 94 | 18 | 28 |
| 300–450 | 187 | 69 | 118 | 33 | 36 |
| 450–600 | 343 | 88 | 255 | 22 | 66 |
| 600–750 | 291 | 108 | 183 | 30 | 78 |
| 750–900 | 380 | 115 | 265 | 51 | 64 |
| 900–1050 | 345 | 133 | 212 | 35 | 98 |
| 1050–1200 | 444 | 113 | 331 | 21 | 92 |
| 1200–1350 | 369 | 70 | 299 | 5 | 65 |
| 1350–1500 | 279 | 14 | 265 | 6 | 8 |
| 1500–1650 | 354 | 22 | 332 | 1 | 21 |
| Total | 3220 | 804 | 2416 | 239 | 565 |

Immutable `publications/ring10/` and append-only metadata/programs are mirrored
to OCI. Elapsed: 27,995.6 seconds, 8.7 seconds per classified
candidate. Latest publication interval: 879.0 seconds / 354 candidates =
2.5 seconds per candidate, including preparation, unavailable-view rejections and
publication. Per-view batch timing remains separate. Weekly account quota is 60%
(27 points above the measured start); reading continues with the 82% stop threshold.

## Twelfth publication: through 1,800 m

The 1,650–1,800 m ring adds 19 characterized walls (3 painter eligible,
16 descriptive/deferred) and 342 rejections among 361 candidates. New programs
cover paired upper windows, an ochre timber shopfront with two separate doors,
and a seven-row brick frontage. Descriptions retain office shade shelves,
curtain walls and projecting balcony planes.

Current 2026 views for building `1033378822` show open construction ground and
an excavator where the 2014 concrete-panel frontage appeared. Two historical
readings are preserved as dated, explicitly non-rendering descriptions; current
empty-wall views are rejected. No historical program is applied to the replacement
or background building.

| Route ring (m) | Candidates | Characterized | Rejected | Painter eligible | Deferred |
|---|---:|---:|---:|---:|---:|
| 0–150 | 88 | 26 | 62 | 17 | 9 |
| 150–300 | 140 | 46 | 94 | 18 | 28 |
| 300–450 | 187 | 69 | 118 | 33 | 36 |
| 450–600 | 343 | 88 | 255 | 22 | 66 |
| 600–750 | 291 | 108 | 183 | 30 | 78 |
| 750–900 | 380 | 115 | 265 | 51 | 64 |
| 900–1050 | 345 | 133 | 212 | 35 | 98 |
| 1050–1200 | 444 | 113 | 331 | 21 | 92 |
| 1200–1350 | 369 | 70 | 299 | 5 | 65 |
| 1350–1500 | 279 | 14 | 265 | 6 | 8 |
| 1500–1650 | 354 | 22 | 332 | 1 | 21 |
| 1650–1800 | 361 | 19 | 342 | 3 | 16 |
| Total | 3581 | 823 | 2758 | 242 | 581 |

Immutable `publications/ring11/` and append-only metadata/programs are mirrored
to OCI. Elapsed: 30,727.9 seconds, 8.6 seconds per candidate.
Latest publication interval: 2,732.3 seconds / 361 candidates = 7.6 seconds per
candidate, including preparation, unavailable-view rejections, waits and publication.
Per-view batch timing remains separate. Weekly account quota is 61% (28 points
above the measured start); reading continues with the 82% stop threshold.

## Thirteenth publication: through 1,950 m

The 1,800–1,950 m ring adds 44 characterized walls (12 painter eligible,
32 descriptive/deferred) and 337 rejections among 381 candidates. New programs
include historic timber window rhythms, paired rendered windows and continuous
window ribbons. Alternate views recover a low courtyard passage frontage and
resolve a narrow industrial window stack. Boarded service openings, projecting
glass balconies and irregular entrances remain descriptive; existing artwork is
preserved.

| Route ring (m) | Candidates | Characterized | Rejected | Painter eligible | Deferred |
|---|---:|---:|---:|---:|---:|
| 0–150 | 88 | 26 | 62 | 17 | 9 |
| 150–300 | 140 | 46 | 94 | 18 | 28 |
| 300–450 | 187 | 69 | 118 | 33 | 36 |
| 450–600 | 343 | 88 | 255 | 22 | 66 |
| 600–750 | 291 | 108 | 183 | 30 | 78 |
| 750–900 | 380 | 115 | 265 | 51 | 64 |
| 900–1050 | 345 | 133 | 212 | 35 | 98 |
| 1050–1200 | 444 | 113 | 331 | 21 | 92 |
| 1200–1350 | 369 | 70 | 299 | 5 | 65 |
| 1350–1500 | 279 | 14 | 265 | 6 | 8 |
| 1500–1650 | 354 | 22 | 332 | 1 | 21 |
| 1650–1800 | 361 | 19 | 342 | 3 | 16 |
| 1800–1950 | 381 | 44 | 337 | 12 | 32 |
| Total | 3962 | 867 | 3095 | 254 | 613 |

Immutable `publications/ring12/` and append-only metadata/programs are mirrored
to OCI. Elapsed: 31,734.9 seconds, 8.0 seconds per candidate.
Latest publication interval: 1,007.0 seconds / 381 candidates = 2.6 seconds
per candidate, including preparation, unavailable-view rejections, waits and publication.
Per-view batch timing remains separate. Weekly account quota is 62% (29 points
above the measured start); reading continues with the 82% stop threshold.

## Fourteenth publication: through 2,250 m

Two further complete rings add 48 characterized walls (14 painter eligible,
34 descriptive/deferred) and 557 rejections among 605 candidates. New programs
cover segmental ground-window heads, pedimented timber surrounds, an eleven-window
frontage with two entrances, and varied paired residential rhythms. Descriptions
retain tall blank end walls, flush French rails, corrugated metal cladding,
glass-block panels, boarded service openings and short horizontal window groups.
Alternate views resolve several occlusions; scaffold-covered and fully concealed
walls remain explicit rejections. Existing facade artwork is preserved.

| Route ring (m) | Candidates | Characterized | Rejected | Painter eligible | Deferred |
|---|---:|---:|---:|---:|---:|
| 0–150 | 88 | 26 | 62 | 17 | 9 |
| 150–300 | 140 | 46 | 94 | 18 | 28 |
| 300–450 | 187 | 69 | 118 | 33 | 36 |
| 450–600 | 343 | 88 | 255 | 22 | 66 |
| 600–750 | 291 | 108 | 183 | 30 | 78 |
| 750–900 | 380 | 115 | 265 | 51 | 64 |
| 900–1050 | 345 | 133 | 212 | 35 | 98 |
| 1050–1200 | 444 | 113 | 331 | 21 | 92 |
| 1200–1350 | 369 | 70 | 299 | 5 | 65 |
| 1350–1500 | 279 | 14 | 265 | 6 | 8 |
| 1500–1650 | 354 | 22 | 332 | 1 | 21 |
| 1650–1800 | 361 | 19 | 342 | 3 | 16 |
| 1800–1950 | 381 | 44 | 337 | 12 | 32 |
| 1950–2100 | 285 | 22 | 263 | 7 | 15 |
| 2100–2250 | 320 | 26 | 294 | 7 | 19 |
| Total | 4567 | 915 | 3652 | 268 | 647 |

Immutable `publications/ring14/` and append-only metadata/programs are mirrored
to OCI. Elapsed: 32,789.3 seconds, 7.2 seconds per candidate.
Latest publication interval: 1,054.4 seconds / 605 candidates = 1.7 seconds
per candidate, including preparation, unavailable-view rejections, waits and publication.
Per-view batch timing remains separate. Weekly account quota is 63% (30 points
above the measured start); reading continues with the 82% stop threshold.
