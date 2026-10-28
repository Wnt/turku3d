# Hybrid facade describer: incomplete truth set, no queue rollout

2026-10-28, `cvr-hybrid`, shared M1 Max. **The requested truth100 was not achieved.**
The final independently reviewed pilot contains six agreed walls. Hybrid storeys tie the
register on this pilot; colour improves. This is insufficient evidence to replace the kit's
background queue. No hybrid queue integration, Astra scheduling change or production
parameter-store write was made. The opt-in engine, reference-height repair and reproducible
collection/evaluation tooling are implemented and approved for landing as an opt-in slice.
The remaining truth-set work and conditional queue rollout are follow-ups.

## Per-field result

These are six walls from six buildings outside the original 425-wall inventory's 60 buildings,
not a 100-wall result. Counts/material require exact agreement. Colour is median CIE76 error
against the midpoint of two independent high-effort Astra estimates. Upper columns exclude
the one single-storey wall. All six have predictions from every method.

| Method | Storeys /6 | Upper columns /5 | Material /6 | Median colour ΔE76 |
| --- | ---: | ---: | ---: | ---: |
| Register fallback | 4 | 2 | 4 | 15.09 |
| Hybrid, building-held-out | 4 | 2 | 5 | 9.29 |
| Astra low | 6 | 5 | 6 | 3.98 |

The hybrid abstained on none of these six already screened, agreed inputs. This tiny selected
sample cannot calibrate rejection rates, establish a storey advantage, or estimate general
accuracy. Agreement between two calls to the same model is not independent human truth.
Astra low uses the existing fast-description field definitions, with six walls batched in
one call; the high reviewers use an explicit stricter evidence screen and never see either
one another's answers or the local predictions.

## Engine

`facade_fast describe --engine hybrid` provides:

- Grounding DINO tiny storeys, `window. door.`, shortest edge 800, box/text threshold .15.
- A standardised ridge head on frozen DINOv2 CLS + mean-patch embeddings for Lab colour.
  Alpha is fixed at 100 from the earlier research, without tuning on this pilot.
- Material-source selection between the embedding head, classic image classifier and register,
  using building-group cross-validation inside each outer training fold. The embedding head wins.
- Columns from width / 2.6 m for wood or pre-1920 buildings, otherwise width / 3 m. No periodic
  override: the earlier experiments did not establish reliable confidence for one.
- Per-field `{state, value, reason}` outputs. Unsupported fields have `value: null`; only those
  fields fall back to the target wall's register description. Narrow/low-resolution crops,
  insufficient facade/opaque-wall support, missing detected rows and foreground occlusion are
  rejected. A vertical chromatic discontinuity in both height halves is a conservative
  multiple-surface heuristic; explicit wrong-wall/multiple-surface audits also cause rejection.

The evidence mask uses the already pinned ADE20K model. Thresholds are heuristics, not
probabilities. A different building of similar appearance can pass the pixel screen; geometry
reference matching and human/model review remain necessary. All results retain `needs_review`.
The bulk description resolver preserves hybrid field fallbacks, records `source=hybrid`, and
does not turn an unreviewed hybrid reading into a donor for other walls.

Of the old direct-reference labels, 78 have width at least 6 m; 61 pass the local semantic
cleanliness screen. Their original JPEG hashes are verified. Outer evaluation folds hold out
entire new buildings; fitting uses the 61 old labels plus the other new buildings. Material
selection is nested within each training fold. Final shipped numerical weights are refitted
on all **67 retained labels across 36 buildings**, after scoring. The three agreements from
the discarded uncalibrated trial are archived separately and are not training/evaluation data.
No pretrained weights, image embeddings, labels, crops or per-wall predictions are committed.

## Evidence acquisition and why truth100 stopped

The first selection contained 240 wide walls outside the 425-wall buildings. The existing
sharpness/black-pixel checks accepted 172, but the first independent readings rejected most.
Two locally started annotation runners were stopped with their descendants after discovering
wrong-height references; completed results and interrupted-call logs were retained. There
were 36 paired completed readings and only three agreements in this discarded trial.

One inspected Look Around panorama reported camera elevation **26.316 m** beside a wall with
base **5.264 m N2000**. Local maanpinta key points fit ground **4.377 m** at the camera. The
existing assumed 2.4 m camera height gives **6.777 m**, a **19.539 m** difference. Re-rendering
the same panorama with that supported height changes pavement into facade. The shared refs
path now uses a supported local ground plane for Look Around, retains provider elevation,
records the effective camera-height source, and invalidates caches when terrain files change.
Unsupported planes retain the explicitly uncalibrated provider height. Pitch/roll and horizontal
pose are unchanged; no global datum offset was guessed.

A corrected pass selected 185 street-facing segments; 174 passed the pixel screen. A 24-wall
pilot still contained heavy foliage, cropped extents and mixed surfaces. One reviewer batch
timed out after 900 seconds and contributes no accepted labels. The other 12 paired readings
produced no agreements under the evidence criteria.

The broader local inventory then used the cached WFS area's GroundSurface/RoofSurface data,
merged footprint runs, unobstructed street rays and **edge-specific eaves heights** instead of
whole-building mean roof heights. It produced 942 candidate street walls across 1,003 input
building groups. The nearest eligible buildings were visited first, at most one wall per
building before taking another. The original 60 buildings remain excluded.

Of 260 selected wider-area walls, 255 eventually passed the existing pixel checks. While
preparation continued, the first 87 screened inputs were checked semantically; 68 passed.
Both high-effort reviewers labelled all 68 independently: **six agreed**, 61 failed the joint
evidence requirement and one disagreed on material. The other 187 prepared walls have not
received paired labels. Rejections include foliage/vehicles, multiple wall surfaces, horizontal
pose/extent mismatch, clipped heights and blank walls with unreadable floor counts. We did not
reinterpret rejected readings as clean or continue buying labels at this acceptance rate.

Acceptance is fixed before scoring: both reviewers mark the target usable, neither flags a
wrong wall or multiple surfaces, occlusion is at most 20%, storeys/columns/material agree
exactly and colour estimates differ by at most 8 ΔE76. Input hashes and exact returned IDs
are checked before reuse. The six accepted labels and their sources remain under
`~/turku3d/research/facade-cv/truth100/whole/`; earlier trials remain under the parent directory.

## Runtime, calls and quota

Measured feature extraction averaged **1.171 s/wall** on the six accepted walls, with image
decoding/model loading excluded. An actual five-wall dispatcher smoke, including crop handling
and output writing after model loading, took **6.214 s**, or **1.243 s/wall**. It completed with
all socket connections forbidden: five valid descriptions, zero failures, zero calls. The
real bulk resolver also preserved the exact field-resolved descriptions. Including model
startup, the command took 23.0 s; these are shared-host timings, not gate assertions.

**Queue buildings/minute is not measured**, because the deployment condition did not pass.
Local inference spends **zero quota/hour**. The unchanged queue permits at most 12 Astra
attempts/hour (at most 0.2 building upgrades/minute even if every attempt succeeds) and pauses
at 60% quota. This task did not run the live queue or claim the inference rate as queue throughput.

Annotation/evaluation started 45 calls: 38 succeeded, one timed out, six were interrupted while
stopping the initial bad-reference run. The ledger records 917,546 input tokens, including
193,920 cached input tokens, and 53,636 output tokens, including 28,701 reasoning tokens,
across 39 known-usage streams (one interrupted runner had a completed token stream). The
timeout and five remaining interrupted calls have unknown usage, not zero cost. Missing
interrupted attempts were backfilled into the append-only ledger from their event logs.
Account-wide usage was 63% at task start and 72% at the last completed annotation
snapshot, below the 80% stop. Other tasks and this worker were active; that change cannot be
attributed to annotation alone, and the initial 0.5–1% allocation cannot be claimed as met.
Further annotation expansion was stopped. No API keys, paid API service or new model weights
were introduced.

## Verification and continuation

Exact setup, offline-engine, collection and evaluation commands are in
[textures/CV.md](../../textures/CV.md#field-wise-hybrid-and-independent-truth-collection).
Aggregate data: [facade-cv-hybrid-metrics.json](facade-cv-hybrid-metrics.json).
Local detailed outputs: `$TURKU3D_WORK/reports/hybrid/`; labels/crops stay in the research root.
Only pure synthetic tests were added, covering field fallback, unsupported evidence, colour
boundaries, label agreement, building grouping and supported terrain/edge-height fits.

To complete the brief, obtain 94 additional agreed usable new-building walls, first repairing
reference identity/extent and selecting unobscured views. Then repeat the held-out comparison.
Only if storeys and colour both beat the register should the local-first queue and telemetry
ranked Astra refinement be implemented and their actual buildings/minute and quota/hour measured.
