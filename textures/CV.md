# Local facade measurements

### Local CV describe experiment

`facade_fast describe --engine cv` reads the existing wall-plane-rectified crops without network calls or quota use; install with `uv pip install --python .venv/bin/python -e './textures[cv]'`. Use a separate run directory with its own `plan.json`: `.venv/bin/python -m turkutex.facade_fast describe --engine cv --run "$RUN" --inventory "$FACADES" --refs "$REFS"`. Classic OpenCV measures Lab clusters, opening projections and appearance rules; `--segmentation segformer` adds NVIDIA's ADE20K building/occluder mask after the explicit download `.venv/bin/python -m turkutex.facade_cv prepare-model` (15 MB, immutable publisher revision, `~/turku3d/tools/cv/segformer-b0-ade/`). Inference always loads local files. The unchanged description schema goes to `run_describe/out.jsonl`; `confidence/<wall>.json` and record/render sidecars carry uncalibrated per-field confidence and `needs_review`. This experiment failed the original 20-wall accuracy threshold, so Astra remains the default and CV results must be reviewed before entering the bulk parameter store. Roof/material/features use weak appearance rules; balconies, arcades, and non-plaster/non-brick materials remain unresolved. Reproduce the numeric benchmark with `.venv/bin/python -m turkutex.facade_cv_benchmark --fixture "$FIXTURE" --assembly "$TURKU3D_DATA/facade-approvals/assembly" --output "$TURKU3D_WORK/reports/cv.json" [--segmentation segformer]`, where `FIXTURE` is the original fast pass's `work/textures/fast-measure` directory; all imagery remains local.

`--engine cv-priors` adds an offline research variant with register/geometry tree heads and
an appearance-to-colour ridge head, exported as JSON. It reads the existing
`raw/wfs/work-3x3` register and needs NumPy, OpenCV, SciPy and the usual texture dependencies;
it does not import torch or sklearn at describe time and needs no pretrained download.
Use a separate run: `.venv/bin/python -m turkutex.facade_fast describe --engine cv-priors
--run "$RUN" --inventory "$FACADES" --refs "$REFS"`. Score with
`.venv/bin/python -m turkutex.facade_cv_benchmark --engine cv-priors --fixture "$FIXTURE"
--assembly "$TURKU3D_DATA/facade-approvals/assembly" --output "$TURKU3D_WORK/reports/priors.json"`.
Confidence sidecars identify the 33-wall held-out pseudo-label calibration and always require
review; this variant also fails the count target, so Astra remains the default. Lidar and
frozen DINOv2 experiments, grouped splits, learning curves, pinned preparation commands and
the distinction between 278 direct labels and 147 fallbacks are documented in
[the priors report](../docs/research/facade-cv-priors.md).

### Local facade model research

`facade_fast describe --engine cv-models` is the opt-in combination of publisher-pinned
Grounding DINO tiny storeys (800 px, opening-only query) and CMP SegFormer columns/colour.
It always marks output `needs_review`; Astra remains the bulk default because columns and
colour fail the reviewed benchmark. See [the model study](../docs/research/facade-cv-models.md)
for accuracy, runtime, limitations and all ablations. No inference command downloads weights.

```sh
source ../sandbox.env
uv pip install --python textures/.venv/bin/python -e './pipeline' -e './textures[cv-models]'
textures/.venv/bin/python -m turkutex.facade_models prepare grounding-dino
textures/.venv/bin/python -m turkutex.facade_models prepare cmp
# Use a separate run directory containing its own plan.json.
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 textures/.venv/bin/python -m turkutex.facade_fast describe \
  --engine cv-models --run "$RUN" --inventory "$FACADES" --refs "$REFS"
textures/.venv/bin/python -m turkutex.facade_models_benchmark --engine cv-models \
  --fixture "$FIXTURE" --assembly "$TURKU3D_DATA/facade-approvals/assembly" \
  --output "$TURKU3D_WORK/reports/models/reviewed.json"
textures/.venv/bin/python -m turkutex.facade_models_benchmark --engine cv-models \
  --pseudo-root "$HOME/turku3d/research/facade-cv" \
  --output "$TURKU3D_WORK/reports/models/pseudo.json"
```

Caches live under `~/turku3d/tools/cv/cvr-models/` with a 10 GB preparation cap.
Other selectable experiments: `cmp`, `cmp-grid`, `grounding-dino`,
`grounding-dino-openings`, `smolvlm`, `qwen`. VLM experiments require their explicit
`prepare` download and constrained JSON dependencies from `cv-models`; Qwen's full schema
prompt is the CLI default, while SmolVLM uses the short prompt and classic estimates for
unqueried fields. Benchmark `--short` tests the compact five-field VLM prompt; `--size`
controls VLM thumbnail side or detector shortest edge. Florence needs its separate pinned
runtime; [the research scripts](../tools/research/facade_models/README.md) reproduce it and
the material probe. None of the experimental confidence scores is a calibrated probability.

### Facade input transforms

The refs rectifier directly samples panorama rays at wall-plane pixel centres. Its reusable
API is `streetview.rectify.rectified_points(corners, pixels_per_metre=32, max_side=2048)`
and `sample_wall(rgb, camera_model, points, camera_position)`. Coordinates are ENH; corners
are bottom-left, bottom-right, top-right, top-left. The existing long-side API remains available.
Re-render a saved pose from a local panorama (including a higher resolution of the same panorama):

```sh
PYTHONPATH=textures:pipeline .venv/bin/python -m turkutex.streetview.rectify \
  --view "$REFS/$WALL/$VIEW.json" --panorama "$PANO" \
  --pixels-per-metre 32 --max-side 2048 --output "$TURKU3D_WORK/reports/wall.png"
```

The PNG and coverage mask are local street imagery and must never be committed. A source-coverage
mask does not detect occluders. Rectification uses the recorded provider pose conventions,
including their existing pitch/roll limitation; it cannot fix an inaccurate pose or wrong wall.

Transform ablations use the same scorer and the exact original Astra crop bytes. The bulk
runner recreates `crop_for_call` and verifies all direct-reference SHA-256 values before
measuring anything. It reports the 278 direct readings separately from the 425-wall inventory
with donor/register fallbacks, and also reports direct walls at least 3 m wide.

```sh
source ../sandbox.env
export PYTHONPATH=textures:pipeline
# FIXTURE is the original fast-measure directory; REFS is its original textures/refs sibling.
.venv/bin/python -m turkutex.facade_transform_benchmark \
  --fixture "$FIXTURE" --refs "$REFS" --output "$TURKU3D_WORK/reports/transforms" \
  --variants baseline metric16 metric32 metric64 native16 native32 native64 \
  clahe shade whitebalance median vote periodic contrast
.venv/bin/python -m turkutex.facade_transform_benchmark \
  --labels "$HOME/turku3d/research/facade-cv/labels" \
  --refs "$HOME/turku3d/research/facade-cv/refs" \
  --output "$TURKU3D_WORK/reports/transforms-bulk" --variants baseline periodic
```

Add `--segmentation segformer` for the pinned ADE20K baseline, or `--engine cv-models`
for the separately prepared local detector ensemble. Detector comparisons accept the input
transforms; `periodic` and `contrast` specifically replace the classic opening-grid reader.
`native*` samples the largest already-cached resolution of the same panorama onto the wall at
16/32/64 px/m (1536 px side cap); it never downloads. Panorama decode/rectification is an offline
preparation cost recorded in local sidecars and excluded from describe latency. All other
transform work and additional-view reads are included. Benchmark JSON is overwritten on each
invocation; run only the desired `--variants` when continuing an experiment. Images stay local.

`facade_fast describe --engine cv-transforms --run "$RUN" --inventory "$FACADES" --refs "$REFS"`
retains the metric `periodic` reader as an opt-in research engine, with optional
`--segmentation segformer`. It uses the shared description writer and always requires review.
Count agreement on the bulk pseudo-labels improves, but reviewer accuracy remains below target,
so Astra remains the default. `--engine cv-models --variants modelgrid` in the transform benchmark
tests column votes across metric upper-storey bands while preserving DINO's storey count.
The benchmark records unsupported model inputs as failures without removing their count targets;
colour is scored only where a prediction exists, with measured coverage reported explicitly.
See [the transform study](../docs/research/facade-cv-transforms.md) for the measured outcomes.

### Look Around camera elevation

Reference preparation uses the local N2000 `raw/laz/maanpinta` key-point plane at each
Look Around camera plus the existing 2.4 m camera-height estimate. A six-point minimum,
5 m nearest-point / 20 m neighbourhood limit, slope and residual checks reject unsupported
terrain fits. Without support, the recorded provider elevation remains explicitly uncalibrated.
Raw provider heights remain in `pano.elevation`; the effective height and source are recorded
in `camera.pos_EN_H` and `camera_height_source`. Reference cache keys include nearby ground
file identities, so new ground data invalidates an earlier uncalibrated crop. Re-run the
normal `facade-batch refs` command to regenerate older references. This fixes observed
19.5 m vertical errors. Current references also apply provider pitch/roll and true-to-grid
convergence, check LOD2 neighbour visibility and target-plane support, screen semantic
foreground and crop-edge openings, and trim excessive roof/gable height to the supported
wall eaves. Full-wall views are sampled across available imagery sources before detail
views, with up to four stored views; a high geometric score in one source no longer
crowds out a complete wall in another source. The reference key includes these geometry,
selection and screen versions.
Verify without imagery: `PYTHONPATH=textures:pipeline .venv/bin/python -m pytest -q
textures/tests/test_streetview_height.py textures/tests/test_streetview_geometry.py`.

For an independently reviewed local reference rate on a fixed inventory, run the
following after acquiring references. Repeat with the old reference root for a paired
before/after comparison; only `aggregate.json` may be published.

```sh
source ../sandbox.env
source ~/turku3d/envs/pipeline.env
PY=.venv/bin/python
INV="$TURKU3D_WORK/reports/references/source-diverse425/candidates.geojson"
REFS="$TURKU3D_WORK/reports/references/source-diverse425/refs"
AUDIT="$TURKU3D_WORK/reports/references/audit425-v12"
$PY -m turkutex.facade_reference_audit prepare --inventory "$INV" --refs "$REFS" --output "$AUDIT"
scripts/dev/codex-task wait-cmd -- $PY -m turkutex.facade_reference_audit label \
  --output "$AUDIT" --reviewer a --batch-size 12 --parallel 1
scripts/dev/codex-task wait-cmd -- $PY -m turkutex.facade_reference_audit label \
  --output "$AUDIT" --reviewer b --only-a-eligible --batch-size 8 --parallel 1
$PY -m turkutex.facade_reference_audit collect --output "$AUDIT"
```

The label commands resume by exact crop/context hash after a transient 429; finish
reviewer A before starting the `--only-a-eligible` second pass.

### Field-wise hybrid and independent truth collection

`facade_fast describe --engine hybrid` combines Grounding DINO tiny at 800 px/.15 with
frozen DINOv2 embeddings and a numeric ridge colour/material head. Material-source selection
compares the head, classic classifier and register on building-held-out training folds.
Columns use width divided by the selected material/era bay spacing. No periodicity override
is enabled: the research did not establish a reliable confidence threshold for it.

The model needs the existing pinned `grounding-dino`, `cvr-priors/dinov2` and ADE20K caches;
prepare them with the existing model commands above and `python -m turkutex.facade_priors
prepare-model`. Inference only reads local weights. Install the existing `cv-models` extra and
`cv-research` for training. Source both `../sandbox.env` and
`~/turku3d/envs/pipeline.env` when using the native environment, including its PROJ data.

Each confidence sidecar contains `fields.<name> = {state, value, reason}`. An abstaining
field has a null value; the schema-valid description retains that field's register value.
Semantic foreground/opaque-wall support, narrow crops, missing opening detections, and
vertical chromatic boundaries cause abstention. These are evidence heuristics, not calibrated
probabilities or proof of target identity. Explicit wrong-wall/multiple-surface audits can
also be passed to `Engine.measure(..., audit=...)`. `facade_fast descriptions()` preserves
the resolved fields as `source=hybrid` and does not propagate them as building donors.
All records retain `needs_review`. The background queue applies only fields supported by
the held-out comparison, then spends Astra calls on walls with the most player exposure,
using distance to the wall when exposure is unavailable. See
[the reference and truth report](../docs/research/facade-cv-reference-truth.md) for the
current comparison and [the pilot report](../docs/research/facade-cv-hybrid.md) for the
initial six-wall result.

All truth images, labels, rejected attempts and call logs stay under the local research root.
The two high-effort reviewers never see one another's labels. Acceptance requires both to
find usable single-wall evidence with at most 20% foreground occlusion, exact count/material
agreement and colour distance at most 8 ΔE76. Only agreed labels enter fitting/evaluation.
Both high and low passes batch at most 12 walls per call; low uses the existing fast prompt's
field definitions. These are model-consensus labels, not human ground truth. The two high
passes must be repeated whenever a selected crop changes; the collector verifies crop hashes.

```sh
source ../sandbox.env
source ~/turku3d/envs/pipeline.env
ROOT="$TURKU3D_WORK/reports/truth100"
BULK="$HOME/turku3d/research/facade-cv"
OUT="$TURKU3D_WORK/reports/hybrid-evaluation-final"
OSM_OUT="$TURKU3D_WORK/reports/hybrid-evaluation-osm"
PY=.venv/bin/python
# The inventory derives connected LOD2 wall planes and excludes the existing labelled set.
$PY -m turkutex.facade_hybrid_truth inventory --root "$ROOT/surfaces"
scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py build -- $PY -m turkutex.facade_hybrid_truth prepare \
  --root "$ROOT/surfaces/collection" --inventory "$ROOT/surfaces/whole-walls.geojson" \
  --labels "$BULK/labels" --limit 900
$PY -m turkutex.facade_hybrid_truth prescreen --root "$ROOT/surfaces/collection"
# Acquire and independently review another regional pool when fewer than 100 agree.
$PY -m turkutex.facade_hybrid_truth inventory --root "$ROOT/region" --radius 3000
scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py build -- $PY -m turkutex.facade_hybrid_truth prepare \
  --root "$ROOT/region/collection" --inventory "$ROOT/region/whole-walls.geojson" \
  --labels "$BULK/labels" --limit 700
$PY -m turkutex.facade_hybrid_truth prescreen --root "$ROOT/region/collection"
# Run label a and b separately on each screened collection; use --parallel 1 when the
# model service throttles. Collect each before selecting unique wide walls.
scripts/dev/codex-task wait-cmd -- $PY -m turkutex.facade_hybrid_truth label \
  --root "$ROOT/surfaces/collection" --reviewer a --parallel 1 --max-pct 100
scripts/dev/codex-task wait-cmd -- $PY -m turkutex.facade_hybrid_truth label \
  --root "$ROOT/surfaces/collection" --reviewer b --parallel 1 --max-pct 100
$PY -m turkutex.facade_hybrid_truth collect --root "$ROOT/surfaces/collection"
scripts/dev/codex-task wait-cmd -- $PY -m turkutex.facade_hybrid_truth label \
  --root "$ROOT/region/collection" --reviewer a --parallel 1 --max-pct 100
scripts/dev/codex-task wait-cmd -- $PY -m turkutex.facade_hybrid_truth label \
  --root "$ROOT/region/collection" --reviewer b --parallel 1 --max-pct 100
$PY -m turkutex.facade_hybrid_truth collect --root "$ROOT/region/collection"
$PY -m turkutex.facade_hybrid_truth combine --root "$ROOT/refresh" --limit 103 \
  --source-root "$ROOT/surfaces/collection" --source-root "$ROOT/region/collection"
# Refresh the selected references and re-review every crop whose hash changed.
scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py build -- $PY -m turkutex.facade_hybrid_truth refresh --root "$ROOT/refresh"
for reviewer in a b; do
  scripts/dev/codex-task wait-cmd -- $PY -m turkutex.facade_hybrid_truth label \
    --root "$ROOT/refresh" --reviewer "$reviewer" --parallel 1 --max-pct 100
done
$PY -m turkutex.facade_hybrid_truth collect --root "$ROOT/refresh"
# Add independently reviewed reserve walls if fewer than 100 remain. The reserve
# selector chooses unreviewed walls from buildings absent from the first selection.
$PY -m turkutex.facade_hybrid_truth reserve --root "$ROOT/reserve" \
  --source-root "$ROOT/surfaces/collection" --source-root "$ROOT/region/collection" \
  --exclude-inventory "$ROOT/refresh/candidates.geojson" --limit 80 --min-width 8
scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py build -- $PY -m turkutex.facade_hybrid_truth refresh --root "$ROOT/reserve"
for reviewer in a b; do
  scripts/dev/codex-task wait-cmd -- $PY -m turkutex.facade_hybrid_truth label \
    --root "$ROOT/reserve" --reviewer "$reviewer" --parallel 1 --max-pct 100
done
$PY -m turkutex.facade_hybrid_truth collect --root "$ROOT/reserve"
# If the first reserve falls short, select additional unreviewed regional walls from
# distinct buildings. Add --exclude-inventory for every other candidate pool already
# examined; this run also excluded an exploratory reserve-extra pool.
$PY -m turkutex.facade_wide_reserve \
  --inventory "$ROOT/region/whole-walls.geojson" \
  --exclude-inventory "$TURKU3D_WORK/reports/references/foreground425/candidates.geojson" \
  --exclude-inventory "$ROOT/refresh/candidates.geojson" \
  --exclude-inventory "$ROOT/reserve/candidates.geojson" \
  --exclude-inventory "$ROOT/surfaces/collection/candidates.geojson" \
  --exclude-inventory "$ROOT/region/collection/candidates.geojson" \
  --output "$ROOT/reserve-wide-new/candidates.geojson" --limit 120
scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py build -- $PY -m turkutex.facade_hybrid_truth refresh \
  --root "$ROOT/reserve-wide-new"
for reviewer in a b; do
  scripts/dev/codex-task wait-cmd -- $PY -m turkutex.facade_hybrid_truth label \
    --root "$ROOT/reserve-wide-new" --reviewer "$reviewer" --parallel 1 \
    --label-limit 20 --max-pct 100
done
$PY -m turkutex.facade_hybrid_truth collect --root "$ROOT/reserve-wide-new"
# Combine exactly 100 current-hash agreements, one wide wall per building.
$PY -m turkutex.facade_hybrid_truth combine --root "$ROOT/final" --limit 100 \
  --source-root "$ROOT/refresh" --source-root "$ROOT/reserve" \
  --source-root "$ROOT/reserve-wide-new"
scripts/dev/codex-task wait-cmd -- $PY -m turkutex.facade_hybrid_truth label \
  --root "$ROOT/final" --reviewer low --parallel 1 --max-pct 100
scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py build -- $PY -m turkutex.facade_hybrid_research extract \
  --truth "$ROOT/final" --labels "$BULK/labels" --refs "$BULK/refs" --output "$OUT"
scripts/dev/codex-task wait-cmd -- $PY -m turkutex.facade_register_eval \
  --truth "$ROOT/final" --features "$OUT/features.json" --output "$OSM_OUT"
$PY -m turkutex.facade_hybrid_research evaluate --truth "$ROOT/final" --output "$OSM_OUT"
scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py build -- $PY -m turkutex.facade_hybrid_research smoke \
  --truth "$ROOT/final" --output "$OUT"
$PY -m pytest -q textures/tests/test_facade_hybrid.py textures/tests/test_streetview_height.py
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
```

Extraction is resumable by crop and extractor hashes. The register-enrichment stage uses
the draft's optional OSM and register sources and must write to a different output directory.
Copy its `parameters.json` into `textures/turkutex/facade_hybrid_parameters.json` only after
reviewing the field scores and running the gate. Label reuse checks exact IDs and input
crop hashes; interrupted batches are not accepted. Evaluation rejects missing/stale features,
holds out entire buildings and selects material sources inside the training folds. It writes
local `parameters.json` refitted on all retained training labels after scoring; it never
overwrites the shipped head automatically. `metrics.json` and `predictions.json` distinguish
aggregate scores from local per-wall evidence. Frozen model loading, reference acquisition
and the conditional queue's throughput are separate from the recorded inference timings.
The smoke command forbids socket connections and checks five descriptions through the real
dispatcher and resolver, including preservation of their field-specific register fallbacks.
