# Facade CV: register, geometry, lidar and local distillation

Task `cvr-priors`, 2026-10-28. **No variant reaches the requested 11/14 storeys,
7/10 columns and median colour ΔE76 ≤15 together. Keep Astra as the bulk default.**
Register floors help; lidar openings do not; distillation learns colour much more readily
than counts. The validation-selected offline `--engine cv-priors` takes **0.054 s/wall**
on the original benchmark, but achieves only **6/14 storeys, 3/10 columns, ΔE16.94**.
It remains an explicitly selected research engine with `needs_review: true`.

Code: `textures/turkutex/facade_priors{,_lidar,_research}.py`; exported numerical heads:
`facade_priors_parameters.json`. Complete aggregate numbers and input hashes are in
[facade-cv-priors-metrics.json](facade-cv-priors-metrics.json). Local per-wall scores,
features, embeddings, point crops and grids are under
`$TURKU3D_WORK/reports/priors/`; none of those are committed.

## Data audit and evaluation protocol

The “425 Astra descriptions” are **278 direct reference descriptions, 99 same-building
donor descriptions and 48 register fallbacks**. Direct descriptions span just **46 buildings**;
all 425 span 60. Treating the fallbacks as independent visual labels would reward the
existing register/donor algorithm. Only the 278 direct descriptions train the experiments.
Of these, **151 have wall width below 3 m**. In the inspected `103277348B_8` crop, the
11 cm wall is a vertical sliver of architectural trim; its five-storey label differs from
the adjacent full facade's four-storey label. These are not 151 useful full facades.

All **19 reviewer buildings**, including their other walls, are excluded from fitting and
selection. Normalised register IDs group `gis-` aliases and `-part` components together.
The remaining 235 direct labels cover 41 buildings. Sort building IDs by
SHA256(`priors-2026:<id>`), assign folds by sorted index modulo five:

| Role | Buildings | Direct labels |
| --- | ---: | ---: |
| Fit, folds 0–2 | 25 | 175 |
| Source selection, fold 3 | 8 | 27 |
| Test and reliability calibration, fold 4 | 8 | 33 |

The shipped weights are fitted on the 175 training labels, without a later refit.
Per-field source selection minimises validation error (mean ΔE for colour).
The test fold never changes the weights or selects the field source. It supplies global
per-field reliability estimates after selection. The small number of buildings makes these
estimates uncertain; they are agreement with Astra, not independently verified correctness.
Trees were added as an exploratory second model family after the ridge run; the reviewer
set was never used to choose their hyperparameters or the final source mapping.

The larger table uses five-fold **building-held-out predictions** for the 235 eligible labels;
the 43 direct labels on reviewer buildings are predicted by a fit on all 235 other labels.
Thus no scored wall's building trains its prediction. This is an exploratory model-family
comparison, not an independent estimate of the best model selected from the table.
[Grouped validation rationale](https://scikit-learn.org/stable/modules/cross_validation.html#cross-validation-iterators-for-grouped-data).

Reviewer counts retain the existing evaluator's 14-storey/10-column denominators, original
`fast-measure/inputs`, and register/donor colour fallback. Colour truth is the accepted
generated map's median, not a calibrated photograph. Material has no equivalent independent
reviewer field, so the material column below explicitly measures agreement with the **17
visible original Astra descriptions**. No image-derived crop is published.

## Methods

**Geometry/register.** The unchanged `register_description` supplies height/3.2 m storeys,
2.6–3 m bays, era/material palette, or registered floors. Features add width, height, their
logs and ratio, floor count and missingness, height per registered floor, construction year
and missingness, and material-class indicators. Register coverage on the 60 buildings:
58 floor counts, 56 material strings, 56 years. Neither cached register table has a documented
building-use field, so use is not invented from address or cadastral type codes.

Geometry alone misreads actual facade floors: `103277348B_344` is 19.799 m high but has four
reviewed floors, while `103304670P_39` is 8.475 m with one. A uniform 3.2 m division cannot
describe tall historic floors and roof/gable extent. Register floors are building-wide:
`103277350D_31` has eight registered floors but the reviewed wall has two;
`103304672S_10` has one registered floor but seven reviewed floors. Materials also describe
register classes rather than necessarily the visible finish: masonry can be plastered.

**Image/distillation.** Classic measurements plus Lab quantiles and four horizontal bands'
Lab means/stds form 49 appearance features. Standardised ridge (alpha=100) predicts counts,
Lab colour and material one-hot scores. The nonlinear comparator uses separate 64-tree
ExtraTrees heads, depth ≤8, minimum leaf=3, seed=17; classifiers for counts/material and
regression for Lab. Tree inference is exported to ordinary JSON and NumPy, with no pickle
or sklearn runtime dependency. [ExtraTrees documentation](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.ExtraTreesClassifier.html).

Frozen **DINOv2 ViT-S/14** contributes the 384-dimensional CLS token and 384-dimensional mean
patch token. The entire rectified crop is resized to 224×224 (no centre crop), normalised
with ImageNet mean/std, then evaluated on MPS. This deliberately tests a small global head;
it does not test a spatially supervised detector. Global embeddings and square resizing
can lose the exact repetition/extent needed for counts. The publisher describes frozen
features with linear heads as a supported use:
[Meta model card](https://github.com/facebookresearch/dinov2/blob/main/MODEL_CARD.md),
[publisher checkpoint](https://huggingface.co/facebook/dinov2-small).

**Lidar.** The existing `turku3d.lidar.walls` streams raw LAZ read-only, projects points into
wall-local u/h/depth, and keeps ±0.6 m around the plane. Remove ground, vegetation and noise
classes, and the lowest 0.3 m. At 0.5 m cells, test (a) density below 30% of local smoothed
density where the latter is at least one point, and (b) mean recess ≥0.12 m relative to the
wall's median plane, with at least two observations per cell. Project masks into rows and
upper-wall columns using the existing periodicity routine. Lidar RGB is not used.

Across **445 walls** (425 + 20 reviewer walls), median density is **3.81 points/m²**,
occupied-cell coverage **20.0%**, and cells with ≥3 points **8.64%**. Only **1/445** passes
the prespecified support screen of ≥80% occupied and ≥50% three-point cells. Empty cells
are unobserved, not proven glass. Airborne sampling, oblique incidence, roof overhangs and
vegetation can leave missing facade returns; lidar does not remove occlusion ambiguity.
Density and depth fail the count benchmark below. Adding these features to the ridge head
also fails to improve reviewer counts, so lidar contributes no production confidence.

## Original 20-wall benchmark

All learned variants below use the same 175-label fit. “Image” means the small appearance
feature vector; “DINO + priors” includes appearance as well as geometric/register features.
All rows share classic visibility handling (19/20 visible, versus Astra's 17/20), so visibility
remains another unresolved field. Blank/occluded walls are not safe to publish automatically.

| Variant | Storeys /14 | Columns /10 | Material vs Astra /17 | Median ΔE76 |
| --- | ---: | ---: | ---: | ---: |
| Classic baseline | 4 | 0 | 7 | 26.07 |
| Geometry only | 2 | 1 | 7 | 29.38 |
| Register | 8 | 3 | 10 | 20.90 |
| Ridge priors | **9** | 2 | 10 | 20.57 |
| Ridge image | 1 | 1 | 7 | 16.94 |
| Ridge image + priors | 6 | 1 | 10 | 17.45 |
| Ridge DINO | 5 | **4** | 8 | 16.20 |
| Ridge DINO + priors | 5 | 1 | 10 | **14.73** |
| Trees priors | 6 | 3 | 7 | 27.71 |
| Trees image + priors | 7 | 1 | 7 | 18.01 |
| Trees DINO + priors | 5 | 3 | 7 | 17.03 |
| Lidar density + register colour/material | 4 | 3 | 10 | 20.90 |
| Lidar depth + register colour/material | 0 | 1 | 10 | 20.90 |
| Ridge image + priors + lidar | 3 | 2 | 10 | 17.28 |
| Validation-selected `cv-priors` | 6 | 3 | 7 | 16.94 |
| Original Astra | 12 | 8 | — | 13.04 |

`cv-priors` selects tree priors for both counts, ridge appearance for colour, and the plaster
majority for material. On validation it gives 13/27 storeys and 14/19 columns; on the separate
test/calibration fold **15/33 storeys, 15/29 columns, 24/33 material, ΔE9.94**. Its poorer
reviewer material result (7/17) exposes the pseudo-label set's material imbalance: the
plaster majority scores 224/278 there, but only 7/17 on the reviewer sample.

Actual preloaded engine timing is **1.082 s / 20 = 0.054 s/wall**, including image loading,
classic measurements and the head. Reference acquisition, startup and training are excluded.
Frozen DINO extraction measured median **0.028 s/crop**, plus **0.023 s** for the small image
features; it was not selected for inference. `benchmark-*` research scores computed from
cached predictions have **no meaningful inference timing**; only `benchmark-engine.json`
runs the actual selected engine. No network or model call occurs in describe.

## Larger-set agreement and sliver sensitivity

These are **Astra agreements**, not real-world accuracy. Counts below use 278 direct labels,
241 multi-storey column labels; wide-wall columns use 106 labels among 127 walls ≥3 m.

| Variant | Storeys /278 | Columns /241 | Material /278 | Median ΔE | Wide columns /106 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Geometry | 51 | 94 | 224 | 18.37 | 35 |
| Register | 125 | 101 | 216 | 15.72 | 39 |
| Classic | 50 | 86 | 224 | 25.03 | 24 |
| Ridge priors | 110 | 102 | 223 | 15.12 | 25 |
| Ridge image | 67 | 74 | 224 | 8.54 | 32 |
| Ridge image + priors | 100 | 89 | 222 | 8.74 | 24 |
| Ridge DINO | 110 | 62 | 230 | 12.51 | 13 |
| Ridge DINO + priors | 120 | 78 | 229 | 9.69 | 21 |
| Trees priors | 94 | 104 | 224 | 16.25 | 29 |
| Trees image + priors | 79 | 111 | 225 | 9.95 | 22 |
| Trees DINO + priors | 55 | 125 | 224 | 10.57 | 27 |
| Lidar density | 56 | 63 | 216 | 15.72 | 16 |
| Lidar depth | 35 | 58 | 216 | 15.72 | 12 |
| Ridge image + priors + lidar | 93 | 96 | 223 | 8.72 | 26 |

Many narrow fragments have zero columns, inflating the all-wall column score. The best wide
result is the simple register-spacing rule's 39/106; there is no evidence that the learned
global representations recover a reliable opening grid.

For completeness, the **425-entry fallback-inclusive** comparison applies register predictions
to missing-image entries for learned/image variants. Geometry and lidar can predict all walls.
Its 147 fallback targets are not independent ground truth; do not quote it as a 425-wall
vision test. All variants are in the metrics JSON; representative results:

| Variant | Storeys /425 | Columns /354 | Material /425 | Median ΔE |
| --- | ---: | ---: | ---: | ---: |
| Register | 225 | 176 | 335 | 15.25 |
| Classic + register fallback | 150 | 161 | 343 | 18.37 |
| Ridge priors | 210 | 177 | 342 | 14.95 |
| Ridge image + priors | 200 | 164 | 341 | 9.08 |
| Ridge DINO + priors | 220 | 153 | 348 | 10.40 |
| Trees priors | 194 | 179 | 343 | 15.86 |
| Trees DINO + priors | 155 | 200 | 343 | 10.92 |

## Learning curve, confidence and next labels

Nested training-building subsets use the same fixed 33-wall/8-building test fold. The subsets
contain 23, 57, 125 and 175 labels, not equal counts of independent buildings.

| Buildings / labels | Tree priors storeys /33 | Tree priors columns /29 | Ridge fusion storeys /33 | Ridge fusion columns /29 | Ridge fusion ΔE |
| --- | ---: | ---: | ---: | ---: | ---: |
| 6 / 23 | 8 | 15 | 8 | 5 | 12.55 |
| 12 / 57 | 14 | 13 | 13 | 6 | 11.04 |
| 19 / 125 | 15 | 14 | 17 | 12 | 10.49 |
| 25 / 175 | 15 | 15 | 17 | 14 | 10.38 |

DINO + priors is non-monotonic: 13, 16, 14, 9 exact storeys and 14, 12, 16, 13 exact columns
at those four sizes. There is **no defensible extrapolation to the target** from this curve.
More slivers from the same buildings are unlikely to provide the missing count evidence.

The selected engine's global calibrated pseudo-label agreement is **0.455 storeys (15/33),
0.517 columns (15/29), 0.727 material (24/33), 0.758 colour within ΔE15 (25/33)**. Sidecars
carry these denominators and the calibration target. They are deliberately constant per
field, not a claim of reliable per-wall ranking; invisible crops get zero confidence for
these fields. Visibility, ornament, roofs, opening proportions and ground-floor features
retain the uncalibrated classic baseline. Every result needs review; no auto-Astra calls
or silent promotions are introduced.

**Recommendation:** one additional **100-wall label batch**, selected across roughly 100
previously unseen buildings, width ≥3 m, balanced for storeys/material, with slivers and
occlusion explicitly flagged. Review at least 30 of those manually and label the visible
wall extent plus window centres/rows, so a spatial counter can be evaluated. This is an
experiment budget, not a promise that 100 labels will meet the target. Reassess before buying
another batch. At the existing fast-pass calibration, 100 Astra descriptions cost about
**0.5 weekly percentage points (roughly 0.25–1.0)**; 200 would be about one point, but the
curve does not currently justify that larger purchase. No additional Astra calls were made.
Account-wide quota readings during this task were 48–51%, below the brief's 80% stop line.

## Downloads and reproduction

Only pretrained download: `facebook/dinov2-small`, revision
`ed25f3a31f01632728cabb09d1542f84ab7b0056`, original publisher namespace, Apache-2.0.
Cache: `~/turku3d/tools/cv/cvr-priors/dinov2/`, **88,251,474 bytes including provenance**:
weights 88,249,960, config 547, preprocessing config 436, remaining provenance JSON 531.
SHA-256 of weights: `ae1e99fcefd534ed978cdeb8326f08030c96e28b7a81ffcbc98a857c84d14be1`.
No other pretrained weights were downloaded; this is well below 10 GB. Training used
scikit-learn 1.9.1, Transformers 4.57.1, PyTorch 2.14.1 and safetensors 0.6.2.

The inherited Conda environment loaded conflicting OpenMP runtimes when geo/scikit-learn
and torch were imported together. The embedding pass uses a clean pip environment; training
uses cached embeddings in a separate process. No duplicate-OpenMP override is enabled.

```sh
source ~/turku3d/wt/cvr-priors/sandbox.env
export PYTHONPATH="$PWD/textures:$PWD/pipeline"
DATASET="$HOME/turku3d/research/facade-cv"
FIXTURE="$HOME/turku3d/wt/textures-fast-facade-pass/work/textures/fast-measure"
OUT="$TURKU3D_WORK/reports/priors"
FACADES="$TURKU3D_DATA/build/layers/buildings/facades.geojson"

# Runtime: no torch, sklearn, pretrained weights or network access needed.
.venv/bin/python -m turkutex.facade_cv_benchmark --engine cv-priors \
  --fixture "$FIXTURE" --assembly "$TURKU3D_DATA/facade-approvals/assembly" \
  --output "$OUT/benchmark-engine.json"

# Explicit preparation, only for reproducing the frozen-embedding experiment.
uv venv --python "$HOME/turku3d/envs/pipeline/bin/python" "$TURKU3D_WORK/cache/priors/embedding-env"
EMBED="$TURKU3D_WORK/cache/priors/embedding-env/bin/python"
uv pip install --python "$EMBED" 'torch==2.14.1' 'transformers==4.57.1' \
  'safetensors==0.6.2' 'opencv-python-headless==5.0.0.93' pillow scipy pyproj pyarrow shapely jsonschema
"$EMBED" -m turkutex.facade_priors prepare-model
"$EMBED" -m turkutex.facade_priors_research extract --embeddings \
  --labels "$DATASET/labels" --refs "$DATASET/refs" --fixture "$FIXTURE" \
  --inventory "$FACADES" --output "$OUT"
.venv/bin/python -m turkutex.facade_priors_research extract --lidar \
  --labels "$DATASET/labels" --refs "$DATASET/refs" --fixture "$FIXTURE" \
  --inventory "$FACADES" --output "$OUT"
uv pip install --python .venv/bin/python 'scikit-learn==1.9.1'
.venv/bin/python -m turkutex.facade_priors_research experiment \
  --labels "$DATASET/labels" --refs "$DATASET/refs" --fixture "$FIXTURE" --output "$OUT"
.venv/bin/python -m turkutex.facade_priors_research benchmark \
  --labels "$DATASET/labels" --refs "$DATASET/refs" --fixture "$FIXTURE" --output "$OUT"

# Schema, split grouping, exported head and lidar abstention checks; no photos/models.
.venv/bin/python -m pytest textures/tests/test_facade_priors.py -q
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
```

Training writes `OUT/parameters.json`; it does not overwrite the shipped parameters.
For ordinary describe, make a separate run with its own valid `plan.json`, then use the
existing `facade_fast describe --engine cv-priors --run ... --inventory ... --refs ...` CLI.
Output remains the unchanged fast-description schema plus per-field confidence sidecars.
