# Facade CV: input transformations

2026-10-28, `cvr-transforms`. Local experiments on this M1 Max. No paid calls,
new keys, model training, or street imagery in this commit.

## Diagnosis before changing the inputs

I inspected all 20 original benchmark crops together and inspected difficult individual
crops at their original size. Several failures are failures of evidence, not perspective:

| Cause | Examples and consequence |
| --- | --- |
| Perspective | Most wall/window lines are already straight. `refs.render_view` already sampled the panorama directly at wall-plane points. Its CV sidecar incorrectly called this a homography; that wording is corrected. Protruding ornaments, gables and foreground objects still violate the single-plane assumption. |
| Resolution | The 20 original crops came from 13k/16k panoramas (one 4000×3000 Mapillary perspective image), then rectified views with a 1536 px long side and a final 768 px crop. The 278 direct bulk readings instead used 114 Look Around 6416 px sources, 147 Google 3328/4096 px sources and 17 Mapillary images around 4000 px. The original reviewer set was already using high-resolution sources. |
| Occlusion | `103295901U_11` is largely behind trees; `103304668M_31` is behind a tree and foreground boundary wall; `gis-103277357M_99` loses much of its wall to a tree; `gis-1032581214_25` has foliage and railings. No rectifier reveals hidden windows. |
| Lighting | `103295896N_8` has strong sun flare; `103295893K_36` and `gis-103277347A_57` have substantial shadow. Dark glass, dark wall and cast shadow are easily confused by thresholding. Grey-world correction also mistakes a coloured facade for a colour cast. |
| Wall extent and meaning | `103304659B_65` is a largely blank end wall despite a nonzero storey target. `1032958849_47` loses its lower facade behind construction barriers. Height alone cannot recover which floors are observable, and architectural bands need not be storeys. |
| Wrong evidence | `1032958849_65` shows pavement and railing. It needs a corrected pose/target or abstention, not a sharper version of the same crop. This experiment does not identify which input geometry or pose is wrong. |
| Multiple surfaces | `103304667L_65` includes its ornamented end wall and a neighbouring light facade; `103295896N_8` contains partial edge windows and neighbouring facade surfaces. A whole-image projection can combine incompatible grids. |

The shared bulk refs are a later two-view pass. They are **not** the original four-view
reviewer refs. Reviewer experiments use `describe_jobs.jsonl`'s original `view_id` and the
original `textures/refs` directory. The bulk runner recreates the exact `crop_for_call`
RGB/resize/JPEG-quality-88 path and verifies **278/278 SHA-256 hashes** against Astra's
recorded input hashes. Reading the JPEG-quality-92 rectified files directly changed baseline
counts substantially, so those preliminary numbers are excluded from the final tables.

## Implemented and measured

`streetview.rectify` extracts the existing direct sampler into a reusable API. It supports
fixed pixels per metre, a bounded output size, and an offline CLI taking a saved pose and
an explicit local panorama. Synthetic tests check metric pixel centres, output caps and
an analytic panorama crossing the horizontal seam. Existing refs uses this same sampler.

This is exact wall-plane sampling **for the supplied camera model**, not calibrated
photogrammetry. Google/Look Around retain the existing heading convention and unapplied
pitch/roll limitation; Mapillary retains its calibrated camera model. Camera pose errors,
LOD2 wall errors and non-planar architecture remain. No new guessed rotation convention
is introduced. The camera field-of-view mask is not a semantic occlusion mask; blank
panorama coverage still needs the refs black-pixel screen.

Variants are deterministic and fitted to no reviewer labels:

- `metric16/32/64`: resample the existing 768 px input to a metric grid, capped at 1536 px.
- `native16/32/64`: sample that grid directly from the largest **already cached** resolution
  of the same panorama and pose. No source replacement, downloading or invented detail.
- `clahe`: contrast-limited Lab luminance equalization; `shade`: subtract smooth luminance
  variation while preserving the image's median level; `whitebalance`: capped grey-world gains.
- `median`: per-pixel median of screened full-wall views on the same wall grid, excluding
  classical vegetation/sky/black pixels. Minority-view occluders can disappear, but pose
  disagreement ghosts edges. Two views are insufficient to identify which disagreement is
  an occluder. No unverified automatic image registration is applied.
- `vote`: count modes with ties resolved by view rank, and a CIELab colour medoid over
  visible answers. It cannot add information when two views disagree; the primary answer wins.
- `periodic`: opening projections smoothed at 0.12 m, with minimum peak spacing 1.8 m
  vertically and 0.7 m horizontally, using the known wall dimensions. `contrast` substitutes
  local dark contrast for opening masks. Neither can distinguish a cornice from a floor by itself.
- `modelgrid`: retain the hybrid detector's storey count, split its semantic window mask
  into upper-storey bands and vote metric column peaks across bands. This tests whether
  whole-facade projections were merging different rows.

Both classic OpenCV and the existing pinned ADE20K SegFormer baseline were measured on
the reviewer set. ADE20K supplies wall/opening and tree/car/person/sky exclusions, which
tests semantic occlusion masking alongside the classical masks. The final landed
`cv-models` ensemble is also tested with the input transformations. Its own learned
window masks and detector remain unchanged.

Super-resolution was not added: the reviewer source panoramas already have their original
resolution, direct higher-density sampling did not consistently improve counts, and missing
wall evidence cannot be restored by upsampling. There is no measured justification for
adding another model or generated window detail here.

## Scoring and limitations

The unchanged shared reviewer scorer has 14 storey targets and 10 multi-storey column
targets. Colour is median Delta-E76 to the accepted generated-map colour, with the
existing donor/register fallback for invisible walls. It is not a calibrated reflectance
measurement. Astra's reference is 12/14 storeys, 8/10 columns and Delta-E76 13.04.

The bulk inventory contains 278 direct Astra readings, 99 building donors and 48 register
fallbacks. Direct agreement is reported separately from all-425 fallback-inclusive
agreement. Of the direct readings, only 127 walls are at least 3 m wide; the remaining 151
include extremely narrow wall fragments. Column denominators are 241 direct, 106 direct
and at least 3 m wide, and 354 fallback-inclusive. These are pseudo-label agreements, not
human-verified accuracy, and zero-column fragments make the aggregate easier.

No training was performed. Variant selection is exploratory, including inspection of the
20 reviewer walls; a gain here is not an independent test-set result. A deployment decision
needs a fresh set of wide, visible, whole-wall examples from different buildings.

Times include image reads and transform/inference work, exclude model initialization,
and were measured with other tasks sharing the host. Native panorama decode/rectification
is an offline preparation cost, recorded separately in local cache sidecars; it is not free.
Bulk times are per inventory wall and also per measured crop in the aggregate JSON.

Only **22/278 direct bulk views** have a higher-resolution version already cached
(17 Apple 16384 px and five Google 13312 px panoramas). The rest of the native bulk
experiment still uses the recorded source resolution. All original reviewer panoramas
were already high resolution. Thus the bulk result is a largest-available-source
experiment, not a claim that all 278 views were upgraded. Across the 371 eligible bulk
views and three densities, 1113 local rectifications took 257.43 seconds in total
(median 0.119 seconds each); these preprocessing seconds are excluded from inference.

Metric aspect preservation exposes another failure: very narrow wall fragments become
1–15 px images, whereas legacy refs enforced a 16 px minimum. Grounding DINO can fail
with `selected index k out of range` on the resulting skinny feature maps. The model
metric64 bulk experiment records 26 failures, 18 among the direct labels. They remain
in count denominators. Colour covers only 260/278 direct outputs, explicitly reported;
all 127 walls at least 3 m wide are measured. Padding/stretching these fragments would
not create missing facade evidence, so this experiment records the unsupported inputs.

## Reviewer results

**No tested combination reaches 11/14 storeys, 7/10 columns and median colour error <=15.** Astra stays the bulk default. The untransformed `cv-models` hybrid remains the strongest overall local engine; transformations trade one field for another.

### classic

| Variant | Storeys /14 | Columns /10 | Median Delta-E76 | Seconds/wall |
| --- | ---: | ---: | ---: | ---: |
| baseline | 4 | 0 | 26.07 | 0.275 |
| metric16 | 1 | 0 | 29.29 | 0.050 |
| metric32 | 3 | 1 | 29.40 | 0.092 |
| metric64 | 4 | 1 | 22.53 | 0.147 |
| native16 | 3 | 1 | 34.10 | 0.041 |
| native32 | 3 | 0 | 17.88 | 0.037 |
| native64 | 4 | 1 | 25.51 | 0.094 |
| clahe | 1 | 1 | 28.85 | 0.155 |
| shade | 1 | 2 | 21.09 | 0.202 |
| whitebalance | 2 | 1 | 35.07 | 0.166 |
| median | 2 | 2 | 23.75 | 1.010 |
| vote | 3 | 0 | 26.17 | 0.721 |
| periodic | 3 | 1 | 29.40 | 0.097 |
| contrast | 0 | 2 | 29.40 | 0.123 |

### segformer

| Variant | Storeys /14 | Columns /10 | Median Delta-E76 | Seconds/wall |
| --- | ---: | ---: | ---: | ---: |
| baseline | 2 | 1 | 20.90 | 0.735 |
| metric16 | 2 | 0 | 22.21 | 0.497 |
| metric32 | 2 | 2 | 24.61 | 0.643 |
| metric64 | 2 | 3 | 21.95 | 0.838 |
| native16 | 4 | 1 | 24.19 | 0.637 |
| native32 | 4 | 1 | 25.48 | 0.663 |
| native64 | 3 | 2 | 24.16 | 1.004 |
| clahe | 2 | 1 | 25.36 | 0.709 |
| shade | 1 | 3 | 21.62 | 0.732 |
| whitebalance | 2 | 1 | 28.66 | 1.023 |
| median | 2 | 1 | 17.73 | 1.316 |
| vote | 1 | 2 | 22.09 | 1.907 |
| periodic | 5 | 1 | 24.61 | 1.012 |
| contrast | 2 | 2 | 24.61 | 0.988 |

### cv-models

| Variant | Storeys /14 | Columns /10 | Median Delta-E76 | Seconds/wall |
| --- | ---: | ---: | ---: | ---: |
| baseline | 11 | 4 | 19.72 | 2.310 |
| metric16 | 8 | 4 | 22.43 | 1.336 |
| metric32 | 8 | 5 | 27.93 | 1.108 |
| metric64 | 9 | 5 | 25.31 | 0.823 |
| native16 | 9 | 3 | 25.62 | 0.658 |
| native32 | 10 | 3 | 26.05 | 0.769 |
| native64 | 11 | 4 | 24.47 | 0.885 |
| clahe | 10 | 3 | 30.37 | 0.738 |
| shade | 9 | 1 | 26.37 | 0.811 |
| whitebalance | 10 | 3 | 29.40 | 0.811 |
| median | 9 | 1 | 19.83 | 0.924 |
| vote | 11 | 2 | 21.20 | 2.147 |
| modelgrid | 11 | 3 | 19.72 | 0.891 |

The hybrid baseline timing here includes its first MPS inference and host contention; the models task measured 1.196 s/wall on the same 20 inputs. Counts and colour match exactly. Timing differences are not treated as quality gains.

## Bulk paired results

All rows below use the exact recorded primary crop. Storey/column counts are exact agreements. The all-425 columns include the existing donor/register policy and are not an independent vision test. Only selected semantic/model variants were carried to the bulk set after the reviewer ablations; all classic variants were measured.

| Engine / variant | Direct storeys /278 | Direct columns /241 | Direct Delta-E76 | All storeys /425 | All columns /354 | All Delta-E76 | s/inventory wall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| classic / baseline | 40 | 83 | 26.24 | 70 | 141 | 23.92 | 0.017 |
| classic / metric16 | 53 | 89 | 27.27 | 91 | 157 | 25.40 | 0.006 |
| classic / metric32 | 49 | 88 | 25.50 | 79 | 147 | 27.40 | 0.011 |
| classic / metric64 | 49 | 82 | 25.29 | 83 | 139 | 23.42 | 0.027 |
| classic / native16 | 65 | 85 | 28.76 | 98 | 150 | 29.85 | 0.012 |
| classic / native32 | 48 | 99 | 24.80 | 83 | 162 | 24.51 | 0.040 |
| classic / native64 | 35 | 80 | 25.66 | 61 | 140 | 25.27 | 0.043 |
| classic / clahe | 41 | 76 | 24.13 | 64 | 129 | 23.51 | 0.018 |
| classic / shade | 37 | 64 | 23.93 | 84 | 117 | 24.61 | 0.025 |
| classic / whitebalance | 41 | 80 | 30.66 | 73 | 133 | 32.14 | 0.020 |
| classic / median | 51 | 81 | 23.96 | 76 | 130 | 22.97 | 0.041 |
| classic / vote | 40 | 83 | 26.24 | 66 | 138 | 24.34 | 0.025 |
| classic / periodic | 61 | 112 | 25.50 | 97 | 180 | 27.40 | 0.015 |
| classic / contrast | 47 | 96 | 25.50 | 93 | 158 | 27.40 | 0.016 |
| segformer / baseline | 37 | 86 | 24.35 | 89 | 138 | 24.16 | 0.715 |
| segformer / native32 | 45 | 92 | 25.39 | 88 | 149 | 25.35 | 0.382 |
| segformer / median | 49 | 84 | 25.72 | 99 | 149 | 25.05 | 0.802 |
| segformer / periodic | 50 | 96 | 23.88 | 102 | 177 | 26.00 | 0.765 |
| cv-models / metric64 | 139 | 99 | 29.62 | 191 | 171 | 31.02 | 0.763 |

For the model metric64 row, direct colour covers 260 outputs; 18 direct failures still count as misses in the 278/241 count denominators. The untransformed hybrid reference from [the model study](facade-cv-models.md) is 139/278 storeys, 114/241 columns, Delta-E76 29.24. Metric64 therefore provides no bulk count improvement and introduces unsupported slivers.

### Direct walls at least 3 metres wide

| Engine / variant | Storeys /127 | Columns /106 | Median Delta-E76 |
| --- | ---: | ---: | ---: |
| classic / baseline | 22 | 22 | 30.15 |
| classic / metric16 | 33 | 27 | 28.51 |
| classic / metric32 | 25 | 26 | 30.45 |
| classic / metric64 | 24 | 24 | 27.96 |
| classic / native16 | 34 | 22 | 28.55 |
| classic / native32 | 27 | 32 | 28.96 |
| classic / native64 | 18 | 20 | 27.67 |
| classic / clahe | 21 | 18 | 27.03 |
| classic / shade | 25 | 14 | 24.29 |
| classic / whitebalance | 23 | 22 | 30.88 |
| classic / median | 29 | 28 | 27.96 |
| classic / vote | 22 | 22 | 30.15 |
| classic / periodic | 29 | 31 | 30.45 |
| classic / contrast | 22 | 21 | 30.45 |
| segformer / baseline | 18 | 24 | 26.03 |
| segformer / native32 | 24 | 31 | 26.04 |
| segformer / median | 27 | 32 | 27.70 |
| segformer / periodic | 27 | 32 | 23.82 |
| cv-models / metric64 | 91 | 37 | 31.58 |

## Recommendation and retained engine

`--engine cv-transforms` retains the small `periodic` metric-grid reader with the existing shared crop/result writer. It improves the classic direct pseudo-label counts from 40/278 storeys and 83/241 columns to 61/278 and 112/241, at 0.018 s per measured crop in this run. On the reviewer set it is only 3/14 and 1/10; optional ADE20K segmentation reaches 5/14 and 1/10. Every output still needs review. This is a useful reusable research baseline, not a replacement for Astra.

Keep direct plane sampling, exact input provenance and metric dimensions. Do not enable median fusion, grey-world colour correction, or additional upsampling in the bulk path: none passed the reviewer targets, and several gains disappear or reverse on paired bulk data. Two-view answer voting usually ties and supplies no extra evidence. Semantic masks improve some fields but confuse windows, trim, wall shadows and foreground objects; periodicity cannot repair that semantic ambiguity.

Next work should label whole visible walls at least 3 m wide from new buildings, explicitly mark occlusion/wrong-wall cases, and distinguish window bays from mullions and architectural bands. Correct or reject the pavement/foreground-dominated references before more model tuning. A new candidate should be evaluated on that fresh set before changing the bulk default.

## Weights, quota and local artifacts

This task downloaded **zero model weights**. It reused the already prepared, publisher-pinned ADE20K, CMP and Grounding DINO checkpoints. No network or model call is made by the retained classic engine; the 20-wall CLI smoke also ran with socket connections disabled (20 valid records, zero failures, zero calls). Existing cache sizes and exact revisions are recorded in [the aggregate JSON](facade-cv-transforms-metrics.json). No task-owned weight cache was created, so the 10 GB weight limit is respected.

Weekly quota was 48% at the start and 62% at completion, below the 80% stop threshold; the reading is account-wide. All photos, rectifications, contact sheets, masks, crop hashes and per-wall experiment records remain under the local sandbox reports directory. Only code, tests, this report and aggregate numbers are committed.

## Reproduce

Source the sandbox environment. The original fixtures remain local; no command below publishes imagery. Dependencies and separately prepared models are documented in [textures/README.md](../../textures/README.md). Use the original reviewer refs directory, not the bulk refs.

```sh
source ../sandbox.env
export PYTHONPATH=textures:pipeline
FIXTURE=/Users/wnt/turku3d/wt/textures-fast-facade-pass/work/textures/fast-measure
OLD_REFS=/Users/wnt/turku3d/wt/textures-fast-facade-pass/work/textures/refs
BULK=/Users/wnt/turku3d/research/facade-cv
OUT="$TURKU3D_WORK/reports/cvr-transforms"

# Reviewer: all classic transformations (repeat with --segmentation segformer).
scripts/dev/codex-task wait-cmd -- .venv/bin/python -m turkutex.facade_transform_benchmark \
  --fixture "$FIXTURE" --refs "$OLD_REFS" --output "$OUT"

# Landed hybrid: all compatible image transforms, then metric band voting.
scripts/dev/codex-task wait-cmd -- .venv/bin/python -m turkutex.facade_transform_benchmark \
  --fixture "$FIXTURE" --refs "$OLD_REFS" --output "$OUT" --engine cv-models
scripts/dev/codex-task wait-cmd -- .venv/bin/python -m turkutex.facade_transform_benchmark \
  --fixture "$FIXTURE" --refs "$OLD_REFS" --output "$OUT" --engine cv-models --variants modelgrid

# All classic bulk variants; exact direct input hashes are checked before inference.
scripts/dev/codex-task wait-cmd -- .venv/bin/python -m turkutex.facade_transform_benchmark \
  --labels "$BULK/labels" --refs "$BULK/refs" --output "$OUT/exact"
scripts/dev/codex-task wait-cmd -- .venv/bin/python -m turkutex.facade_transform_benchmark \
  --labels "$BULK/labels" --refs "$BULK/refs" --output "$OUT/exact" \
  --segmentation segformer --variants baseline periodic median native32
scripts/dev/codex-task wait-cmd -- .venv/bin/python -m turkutex.facade_transform_benchmark \
  --labels "$BULK/labels" --refs "$BULK/refs" --output "$OUT/exact" \
  --engine cv-models --variants metric64

# Pure synthetic verification; no models, panoramas or servers.
.venv/bin/python -m pytest -q textures/tests/test_facade_cv.py \
  textures/tests/test_facade_cv_transforms.py textures/tests/test_rectify.py \
  textures/tests/test_streetview_geometry.py textures/tests/test_mapillary_camera.py
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
```
