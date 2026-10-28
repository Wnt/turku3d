# Per-building facade programs: offline prototype

**Promising as a fast offline descriptor/painter architecture; not ready for automatic
realistic facade production.** Opening detections make the building-specific layouts
recognisable, but windows, retail glazing, balconies, shadows and wrong wall crops still
produce visible mistakes. A larger GPU run would scale these errors, not solve them.
No production generator, stored AI description, facade-kit result or texture library was
changed. No reference pixels, per-building predictions or contact sheets are published.

Measured on the M1 Max, 64 GB, PyTorch 2.14.1 MPS. Code:
`textures/turkutex/facade_program.py` and `tools/research/facade_characterization/`.
[Reproduction commands](../../tools/research/facade_characterization/README.md).
Aggregate evidence: [metrics](facade-characterization-metrics.json).

## Inputs and coverage

The frozen OCI metadata snapshot contained **5,632 metadata records but only 3,079
image-bearing panorama records**, rather than 5,664 downloaded images. Another agent
independently confirmed this distinction and is expanding the cache; these numbers describe
our frozen input, not the current live total. This experiment copied only **388 panoramas,
1,279,868,928 bytes**, into its own sandbox. It used no provider acquisition, API key,
paid service, browser capture or remote GPU.

We ordered the existing whole-wall inventory by distance from the midpoint of the office
and pizzeria, selected **340 buildings**, and tested up to two wide walls per building:
**488 walls, 295 with geometrically supported views, 237 photographed buildings**.
There are **490 rectified views** (up to two per wall). The remaining **103 buildings**
receive rule samples. “Without photos” here means no usable view in this particular
cached selection, not proof that no image exists elsewhere. Narrow walls below 8 m are
excluded from this area experiment; the historical twenty-wall comparison retains its
original wall IDs.

Every selected building has an independently sourced footprint, cadastral district and
register-use code. Positive register floors exist for **333/340**, construction year for
337, and register material for 339. Six zero-floor records and one absent count stay
unknown: no image model re-measures floors. We project **173 OSM entrances** onto the
nearest selected wall within 2 m, preserving their OSM origin. Frontage is the length of
selected street-facing walls, not a survey of every frontage; neighbour height is the mean
of the five nearest inventory buildings. District is the cadastral code prefix, not a
learned architectural style.

## Pipeline and field coverage

1. Select existing cached Google/Look Around views with the existing full-wall LOD2 ray
   visibility and camera-height conventions. Rectify directly onto the wall plane with
   `streetview.rectify`, 24 px/m, maximum side 1,024 px. The preliminary selection has no
   downloaded depth map; geometric visibility and later semantic masks supply occlusion
   screening. A visibility pass does not prove the target is the correct facade.
2. Run the pinned [CMP SegFormer-B0 parser](https://huggingface.co/Marco333/segformer-b0-facade-cmp)
   and [ADE20K SegFormer-B0](https://huggingface.co/nvidia/segformer-b0-finetuned-ade-512-512)
   locally on MPS. The ADE mask excludes sky, vegetation, people, cars and other foreground.
3. CMP alone collapsed to mostly wall labels on many clear images: only 16 useful bay-rule
   donors. A representative CPU/MPS comparison produced identical class counts and nearly
   identical logits, excluding an MPS-only failure on that image. Reuse pinned
   [Grounding DINO tiny](https://huggingface.co/IDEA-Research/grounding-dino-tiny),
   `window. door.`, 800 px, for opening extents. Window score ≥.25, metric size limits,
   foreground rejection and IoU .4 duplicate suppression produce the opening mask.
   Door candidates remain separately sourced from DINO and OSM.
4. Choose a wall's view by visible wall-pixel support. Refit colour outside detected
   windows, derive window rectangles, size/aspect, registered-band sill position, horizontal
   bay centres/count/pitch/symmetry, lower-band colour and glazing share. Preserve every
   observed wall and its evidence, rather than repainting a whole building from one wall.
   The representative wall is only the area/support-ranked donor for empirical rules.
5. Fit an eight-neighbour empirical descriptor distribution over register/geometry/location
   covariates; sample with a SHA-256 seed from stable building ID and field name. Donor
   building order does not affect the result. Target register floors always replace donor
   floors. Sampled walls cannot inherit a donor's world coordinates, photo path or observed
   opening positions. Height/floor conflicts are flagged and excluded from donor fitting.
6. Paint a local 512-pixel diagnostic using rectangles and colours. It deliberately exposes
   missing geometry rather than supplying ornament. Fixed glass/frame drawing colours are
   diagnostic defaults, not claims that those colours were measured.

The available CMP checkpoint has **six merged classes**, not the original twelve-label CMP
ontology: cornice, sill, molding, pillar and decorative elements are merged into wall;
doors and shopfronts are combined. Therefore trim, plinth height, frame colour/divisions,
recess depth, canopy, corner treatment, bay windows and exact architectural style remain
null. Balcony components are retained with low support, not trusted geometry. Registered
material is a prior, while measured visible cladding remains unknown. A year band is a
style prior, not proof of Jugend/classicism/functionalism. Base height currently uses an
explicit equal-storey-height register prior; it is not presented as a measured plinth or
shopfront height. Explicit window rectangles preserve lintel positions, but their placement
within a true storey remains uncertain when the register and visible wall extent disagree.

No compiled helper was needed: neural inference dominates the small NumPy fitting and
CPU painting costs. Model loading is local-only. Panorama/camera/model/extractor content
hashes and crop/detector hashes control measurement reuse; fits and rules are cheap
recomputations. The shared conda environment failed with duplicate OpenMP libraries;
a sandbox-owned Homebrew Python 3.12 venv resolved it without changing the shared environment.

## Accuracy and limitations

A fixed SHA-256 ordering selected twelve walls, one per building, at least 10 m wide and
with model-visible share ≥.75. Predictions were hidden during Codex visual annotation.
Three have unscorable opening semantics (one wrong soffit crop, a curtain wall and a retail
strip); other fields abstain individually where obscured or ambiguous. This is a small,
single-reviewer exploratory check, not a second independent human audit or box-IoU test.

| Field | Exact count | Mean absolute count error |
|---|---:|---:|
| Standalone visible windows | 0/9 | 6.33 |
| Visible door groups, including garage doors | 3/6 | 0.83 |
| Visible upper-window columns/bays | 5/8 | 0.88 |

The window criterion excludes continuous ribbon, balcony and shopfront glazing, plus vents.
The detector frequently mistakes those for ordinary windows. That semantic failure explains
much of the count error and is itself a task failure, not a reason to discard those examples.
One balcony facade produces 43 detected components for 14 standalone windows. The 30-building
contact sheet is selected independently of these manual counts and includes failures.

**Historical twenty walls.** The original fixture directory was removed. All twenty IDs and
original colour targets survived in the `facade-cv-describe` command log, and their reference
crops survive in the shared research inputs. Reconstructed highest-score full views, using
the original 768-pixel JPEG transform, give **median ΔE76 17.89**, with 19 measured colours
and one register fallback. Exact original input hashes cannot be authenticated; this is not
a byte-identical paired improvement claim. The earlier study reported 19.72 for its local
hybrid and 13.04 for Astra, with a target of 15. The targets are accepted generated-map wall
colours, not calibrated photographic albedo. Floors were never predicted here.

**Google reference quarters.** We consume only scored/eligible reports and exclude trim
records. Match the same register building and exactly one measured wall within 15° of the
unsigned reference group normal; ambiguous parallel walls abstain. Comparisons are therefore
small and explicitly coverage-limited. The round-3 sandbox was removed during this work;
we retained the palette agent's previously extracted, hashed group RGB snapshot locally.
No missing captures were silently substituted.

| Reference | Matched groups / buildings | Wall-colour ΔE76 | Diagnostic-render ΔE76 | Existing draft ΔE76 |
|---|---:|---:|---:|---:|
| Round 2 | 3 / 1 | 29.19 | 29.19 | 16.01 |
| Round 3 | 25 / 12 | 21.50 | 25.63 | 22.46 |

These are medians over matched groups; round-3 building-balanced medians are 18.60, 20.32
and 19.70 respectively. The reference groups contain glazing, shadows and unknown capture
epoch/illumination, so they are appearance comparisons rather than calibrated wall-albedo
truth. There is no convincing colour win: the painted result is worse than the existing
draft on both matched subsets. No reference colour was used to fit the prototype.

Rule evaluation holds out the **entire target building**, including all its walls, and compares
with an unconditional empirical sampler. It measures agreement with noisy automatic
characterizations, not correctness on genuinely unphotographed buildings. The final rules retain 141 donors after geometry/storey conflict rejection;
183 building-held-out targets are scored. See the aggregate metrics for their errors. The no-photo
contact sheet shows variety and deterministic register-floor preservation, but also implausible
window proportions and height/floor conflicts. The rules inherit the extractor's errors.

## Throughput and GPU-server scenarios

Measured stages total **243.61 seconds for 295 walls**, or **1.21 walls/s**:
26.17 s JPEG read/hash/decode, 12.29 s rectification, 30.66 s two segmentation models,
160.77 s opening detection, 7.60 s initial fitting and 6.13 s opening refinement.
This includes 490 image views, excludes network transfer, model loading, queue waits and
output encoding, and reflects shared-host serial execution. The diagnostic CPU painter
averages **0.093 ms/wall at 512 pixels**, excluding KTX/atlas work.

| Inventory assumption | Walls at 4 walls/panorama | Mac hours | GPU 2x inference scenario | GPU 4x inference scenario |
|---|---:|---:|---:|---:|
| Frozen 3,079 image-bearing panoramas | 12,316 | 2.83 | 1.72 h | 1.16 h |
| Brief's hypothetical 5,664 image-bearing panoramas | 22,656 | 5.20 | 3.16 h | 2.13 h |

Two to eight walls per panorama gives **1.41–5.65 Mac hours** for the actual snapshot,
or **2.60–10.39 hours** for the hypothetical larger inventory. Wall/panorama ratio is
unknown and views overlap: these are scenario workloads, not a city-wide wall census.
Rented GPU scenarios assume only neural inference accelerates; CPU work stays unchanged.
No CUDA device was measured, no hourly rental price is assumed, and no paid run occurred.

## Descriptor proposal and fast OCI step

The prototype emits one local JSON record per building with this conceptual contract:

```text
schema, building_id, seed, source, needs_review, attributes
storeys {value, source=register|unavailable, support}
material / style_era {value, source=register prior, support}
representative_wall
walls[]:
  wall_id, width_m, height_m, geometry, evidence
  observation {visible_share, wall_share, register_height_conflict, needs_review}
  split {base_height_m, middle_repeats, top_height_m}
  wall {colour, material}
  bays {count, centers_m, pitch_m, symmetry, support, source}
  windows {size_m, aspect, rectangles_m, sill_fraction, frame_colour, divisions, recess_m}
  base {colour, material, plinth_height_m, glazing_share, shopfront, openings_m}
  doors {candidates[], type, canopy}
  trim, corners, balconies, bay_windows, style_era
```

Measured/prior fields carry `{value, source, support}`; null means unmeasured, never false.
Support is an uncalibrated evidence heuristic, not a probability. Coordinates are metres from
the wall's viewed bottom-left corner. Storeys can be null. Rule-sampled descriptors identify
the donor and distance, keep source `rule-sampled`, and contain no inherited photo evidence.
The seed is a decimal string, preserving 64-bit identity in JavaScript. Persist the descriptor
and its rules version so repainting textures cannot resample the building.

The next integration slice should validate this schema, strip local evidence paths, and add
a small descriptor reader plus a facade-kit/atlas adapter. Key painting by descriptor content,
painter version and texture-library hashes. OCI then needs JSON reading, layout primitives,
library texture binding and atlas/KTX encoding only; no torch, detector, model weights or
photos. Preserve existing stored AI and facade-kit assets at higher precedence. Measure the
actual OCI painter and KTX/atlas cost before setting a latency target; the Mac diagnostic
painter alone is not an OCI benchmark. First improve/openly review the offline opening
semantics and illumination handling before making these descriptors a default source.

## Local evidence and next work

All evidence remains under:
`/Users/wnt/turku3d/wt/facade-characterization/work/characterization/`.

- `contact-30.jpg`: thirty photo/program pairs; `contact-index.json` maps cells to walls.
- `contact-no-photos.jpg`: twelve seeded samples without a usable selected view.
- `programs.json`, `sampled.json`, `rules.json`: measured and sampled descriptors.
- `manual-labels.json`, `manual-scores.json`, `reviewed20.json`: local audit details.
- `reference-comparison.json`, `timing.json`: per-group scores and runtime scenarios.
- `raw/`, `doors/`, `panoramas/`: local measurements and the selected immutable inputs.

Remaining work: distinguish ordinary windows from balcony/shop/ribbon glazing; reject wrong
wall/soffit references; estimate illumination separately from paint colour; collect independent
boxes and material/trim labels; validate the descriptor adapter and fast painting on OCI.
The prototype does not justify paying for a larger inference run yet.
