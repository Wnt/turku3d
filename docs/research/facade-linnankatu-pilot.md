# Linnankatu: directly reviewed facade programs

**Promising for recognizable, building-specific layout and material descriptions; not
yet a realistic texture solution or a scalable unattended characterization service.**
The local parser/detector failed the earlier small count audit. On Jonni's revised scope,
we stopped tuning it, retained rectification and MPS foreground masking, and had the
assistant inspect the photos directly. This pilot produces 12 accepted wall programs
on 11 buildings and explicitly rejects one scaffold-covered building. It keeps all
registered storeys unchanged. No production generator integration is included here.

The quick painter exposes recognizable window rhythms but omits relief, arched openings,
awnings, fine mullions and photographic material detail. Its previews are diagnostic,
not GTA-quality acceptance images. Raw photo colours are not calibrated pigments.
The three reference controls below show why they must not override calibrated colours.

Public artifacts: [programs](../../textures/library/facade-programs.json),
[aggregate measurements](facade-linnankatu-metrics.json),
[review compiler](../../tools/research/facade_characterization/review.py).
The [earlier 340-building experiment](facade-characterization.md) contains the learned
conditional sampler, no-photo examples, 30-building sheet, twenty-wall historical
comparison and opening-count accuracy. Its automated descriptors remain unreviewed.

## Coverage and method

The street corridor is Linnankatu between Aurakatu and Eskelinkatu, both sides. An OSM
intersection-derived line selects nearby parallel, outward-facing walls from the existing
whole-wall inventory: 16 wall features on 13 buildings. The existing selector evaluates
15 eligible walls; 13 walls on 12 buildings have usable retained views. One building has
two photographed walls. The missing building is Kristiinankatu 3; the unused second wall
of another building does not remove that building from the pilot. This is approximately
the requested fifteen-building pilot, not a claim of complete street coverage.

We reuse 21 already-copied panoramas from the sandbox; no new images are downloaded and
no browser is opened. Twenty-four views are rectified at 32 px/m, capped at 1,280 px,
then masked by the existing pinned ADE20K SegFormer-B0 on MPS. Ten crops fit on the first
contact sheet, three on the second. The assistant inspected both sheets, enlarged the
crops, and hand-authored window rectangles, bay groups, materials, trim and uncertainty.
`review.py` contains those readings explicitly; it does not run a parser or detector.

The compiler reuses enriched register/footprint/neighbour covariates and three existing
OSM entrance projections. OSM candidates retain their own origin and are not labelled
photo-confirmed. Register construction year, use and material remain separate from
observed cladding/style. Per-building merging keeps both wall programs of Linnankatu 16.
No photo pixels, camera metadata, panorama identifiers or local evidence paths are in
the public programs.

Specific evidence boundaries:

- One facade is hidden entirely by scaffold sheeting in both retained views. ADE reports
  nearly 100% visibility, but the manual review rejects all facade appearance inference.
- Two walls are only 4.01 m and 5.04 m ground-floor strips from eight-storey buildings.
  Their programs describe retail bases only; eight floors must not be compressed into them.
- Aurakatu 6 has one registered storey despite five visible upper opening rows. Linnankatu
  17 also has a register/photo zoning conflict. Counts stay registered; explicit opening
  rectangles stay observed; both are excluded from storey-conditioned rule donors.
- Hoarding, buses, trees and parked cars hide other lower openings. Empty candidate lists
  and null bay counts mean unknown, not evidence that doors/windows do not exist.
- Orthorectification uses a wall plane and supplied camera pose. Alternate views show
  residual pose/perspective shifts. The metre rectangles are approximate observations,
  not a surveyed opening map; no centimetre-level alignment claim is made.

## Checks against photos and reference colours

After authoring, the assistant checked alternate retained views of Linnankatu 16, 17
and 20. Forty upper opening groups were clearly confirmable: fourteen main-front windows,
twelve windows, and fourteen windows respectively. Two additional side-wing windows in
the first program are clipped in the alternate view and remain unscored. Seventeen
main bay positions are confirmable, with the side-wing bay likewise unscored. Three
bookshop entrance groups and the narrow Linnankatu 17 entrance are visible in both views;
the bank door is blocked by a truck in the alternate view. Shopfront doors within large
glazing groups are not exhaustively counted. This is a same-assistant consistency audit,
not independent accuracy or recall. No box-IoU or unseen-building generalization score
is asserted. The earlier blind count audit remains the quantitative model evaluation.

**No Linnankatu pilot building overlaps the retained round-2/round-3 colour measurements.**
We do not substitute a nearby building's score. To check the colour method separately,
SHA-256 ordering (`review-colour-control`) selects three of the earlier twelve matched
round-3 buildings. The assistant selects a visible wall patch on each crop without
looking at the reference target; median patch RGB is compared using the previous
same-register/unique-parallel-wall matching. Five reference groups survive.

| Control | Reviewed patch ΔE76 | Earlier automatic colour ΔE76 | Existing draft ΔE76 |
|---|---:|---:|---:|
| Brick hotel facade | 42.08 | 15.26 | 16.52 |
| Grey metal/glass facade | 45.35 | 54.53 | 15.41 |
| Dark panel facade | 28.74 | 19.15 | 16.15 |
| Building-balanced median | **42.08** | **19.15** | **16.15** |

These are appearance comparisons, not albedo ground truth. Reference groups include
glazing and lighting; source epochs/poses differ. Nonetheless, the result provides no
support for replacing calibrated pigment with hand-selected raw photo RGB. The pilot's
white brick becomes blue in shadow and its green timber becomes pale in sun, visibly
confirming the issue. Colour calibration/explicit pigment must take precedence. No colour
targets were fitted. Local `colour-controls.json` retains exact patches and match counts.

## Time, quota and scaling

Measurements are from this shared M1 Max, not an isolated benchmark. Local GPU stages use
no Codex model quota. The reading interval includes tool calls, descriptor code authoring,
coordination and first preview inspection; it is a practical first-pass authoring rate,
not model inference latency. Later colour checks, documentation, gates and delivery are
excluded. The small recomputation after joining existing OSM/covariates is also excluded.

| Stage | Total seconds | Seconds per candidate wall | Codex quota |
|---|---:|---:|---|
| Cached selection | 1.793 | 0.138 | 0 |
| Model load | 0.185 | 0.014 | 0 |
| Panorama decode | 4.492 | 0.346 | 0 |
| Rectification, 24 views | 1.291 | 0.099 | 0 |
| MPS foreground mask | 1.571 | 0.121 | 0 |
| Crop/mask writing | 0.574 | 0.044 | 0 |
| Whole preprocessing, including sheet overhead | **8.256** | **0.635** | 0 |
| Direct reading, authoring and first preview | **638.015** | **49.078** | Not attributable |
| Program compile + contact sheet | 0.244 | 0.019 | 0 |
| Painter subset, 680 px max, excluding encoding | 0.00730 | **0.000561** | 0 |

The account-wide weekly counter moved from 21% to 23% between snapshots. Other agents
were active and the counter is integer-valued: **the two percentage points cannot be
assigned to these thirteen walls or extrapolated as quota/wall**. The durable task-cost
ledger has no completed entry for this active turn. Future throughput runs should use
isolated, recorded image-reading batches with token usage; exact quota cost is unresolved.

For 3,000 image-bearing panoramas, deduplicate wall IDs first. The pilot's selected
wall/panorama ratio is only 13/21 because it retains alternative views and one street
frontage. It is not a city-wide ratio. The table shows that ratio and two larger scenarios:

| Unique walls/panorama | Unique walls | Mac preprocessing hours | 4× faster GPU-mask scenario | Direct authoring hours |
|---|---:|---:|---:|---:|
| Pilot 13/21 | 1,857 | 0.328 | 0.281 | 25.3 |
| 2 | 6,000 | 1.058 | 0.907 | 81.8 |
| 4 | 12,000 | 2.117 | 1.815 | 163.6 |

Eight walls/panorama would take 4.23 Mac preprocessing hours, 3.63 hypothetical GPU hours,
and 327 direct-authoring hours. Only masking accelerates in the rented-GPU scenario;
decode, rectification, I/O and assistant reading do not. No CUDA hardware was measured,
no rented run occurred, and download/queue/encoding costs are excluded. **A GPU rental
does not solve the bottleneck in this direct-reading workflow.** The earlier fully local
automatic path is faster but failed accuracy, so scaling it is not justified yet.

## Program contract and next implementation slice

The schema is `facade-program-prototype-1`, with stable register building IDs and a
decimal SHA-256 seed. Nullable observations use `{value, source, support}`; support is
an evidence score, not a calibrated probability. The baseline schema is unchanged except
for explicit reviewed scope and optional authored fields:

```text
building.needs_review: false only after direct review of at least one wall
building.review: method, matched-wall scope, independent=false
wall.geometry: p0/p1 (EPSG:3877 E,N), outward normal, base_h (N2000)
wall.observation: scope=full-wall|ground-only, needs_review, register_height_conflict,
                  excluded_rectangles_m, opening_extent, colour_space, notes
wall.bays: count int|null, centers_m, pitch_m, rhythm {value, source, support}
wall.windows.rectangles_m / base.openings_m: [u, v, width, height] in wall metres
wall.trim: cornice scalar metres, string_courses list of metre heights,
           window_surrounds scalar metres, colour
wall.base.variant: shops|windows|doors|plain, or unknown
wall.balconies.value: list of metre rectangles, or unknown
```

The viewed wall's bottom-left is u/v zero and u increases from geometry p0 to p1.
Register storeys do not imply repeated floors inside an explicitly measured wall segment.
Partial, unknown-bay and register-conflicted walls cannot train full-facade rules. The
two reviewed walls of one building must not overwrite each other. Scaffold records remain
unusable. Unmeasured recesses, sill fractions, plinth details, corner treatment and hidden
doors stay null; the renderer must not convert null into observed absence.

The coordinated `facade-archetypes` slice owns production JSON validation, precise wall
matching, authored/sampled archetypes and the existing facade-kit adapter. It has been
given these programs, geometry/scope constraints and the colour failure. OCI should
only read descriptors and paint/encode cached atlases, with no photo model dependency.
Preserve existing per-building AI/facade-kit assets and calibrated/explicit pigment.

To speed the offline work: reuse decoded panos across walls; select one clear view and
request an alternate only for uncertain regions; skip CMP/DINO; use a fixed compact
annotation form rather than writing per-batch code; prioritize shop/corner/landmark walls;
and sample the rest from independently reviewed archetypes. Keep visual review for masks
that miss construction sheeting and do not spend a GPU batch on those failures. A small
independent annotation set and illumination correction are the next accuracy work.

## Local artifacts and reproduction

The three pilot sheets were regenerated on 2026-10-29 in durable local storage:
`/Users/wnt/turku3d/data/reports/facade-pilot/linnankatu/`.

- `reviewed-contact.jpg`: 13 photo/stored-program pairs, including scaffold rejection.
- `read-1.jpg`, `read-2.jpg`: regenerated reading sheets.
- `crops/`: 24 rectified views and their foreground masks.
- `selection.json`, `chosen.json`, `sheet-provenance.json`: local reconstruction evidence.
- `programs.json`: byte-identical copy of the stored public programs, not re-authored.

The original sandbox was deleted. Recovery reused the original corridor selection
from its durable task log, OCI panorama files retained before that pilot, and the
same rectification/masking code. It reproduces 13 walls, 24 views and 21 panoramas.
The original selection/crop hashes were not retained, so byte-identical photographic
recovery cannot be asserted. The earlier automated comparison and colour-control
evidence listed in the original report were not regenerated by this recovery.

```sh
source ../sandbox.env
ROOT="$TURKU3D_DATA/reports/facade-pilot/linnankatu"
PYTHONPATH=textures:pipeline HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py build -- \
  "$TURKU3D_WORK/venv/bin/python" tools/research/facade_characterization/pilot.py --root "$ROOT"
PYTHONPATH=textures:pipeline "$TURKU3D_WORK/venv/bin/python" \
  tools/research/facade_characterization/sheets.py --root "$ROOT" \
  --programs textures/library/facade-programs.json
```

`sheets.py` pairs by exact wall ID and renders the stored programs directly; it
never recompiles the authored readings or runs a detector. Source photographs,
metadata and masks remain outside git. Sandbox removal now archives `work/reports`
to the durable task directory before deleting the sandbox.
