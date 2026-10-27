# Local models for facade descriptions

Measured 2026-10-28 on the shared M1 Max, 64 GB, PyTorch MPS. No combination tested meets
all four “decent” criteria. Grounding DINO reaches **11/14 storeys**, but columns and colour
remain below target. Keep Astra as the bulk default. The opt-in `cv-models` engine combines
DINO storeys with CMP SegFormer columns/colour; all outputs require review.

## Evaluation contract

The 20 reviewed walls are the original `textures-fast-facade-pass/work/textures/fast-measure`
inputs. Scoring uses the existing `facade_cv_benchmark`: 14 known storey counts on
Astra-visible walls, 10 known upper-column counts on multi-storey walls, and median CIE76
wall-colour error on all 20 after the existing building/register fallback. Counts score raw
descriptions, exactly as the original baseline. No new image judgement or paid model call
was used. The reviewed set guided exploratory prompt/resolution choices; it is not an
untouched test set for those choices.

The larger local set has **425 descriptions, but only 278 direct-reference descriptions**.
The other 147 are building/register fallbacks and have no associated direct input crop in
those records. We report their exclusion, not an invented image-model accuracy for 425
images. Every one of the 278 reconstructed input JPEG hashes matches its original Astra
`crop_sha256`. All 278 direct labels say `wall_visible=true`; this set cannot validate
occlusion rejection. Upper columns have 241 eligible pseudo labels. Agreement with these
labels is not independently reviewed accuracy.

Inference times include image preparation, synchronous CPU-readable outputs, and
postprocessing, exclude model loading, and are serial per wall. This is a shared host, not
a controlled hardware speed benchmark. First-use MPS work is included. The short VLM and
Florence trials overlapped briefly with other local work; Qwen full overlapped the start
of the bulk hybrid pass. They are too slow by a wide margin regardless. No speed limit is
asserted in tests or gates.

## Reviewed results

Target: storeys ≥11/14, columns ≥7/10, median colour ΔE76 ≤15, about ≤1 s/wall.

| Variant | Storeys | Columns | Colour ΔE76 | Seconds/wall |
|---|---:|---:|---:|---:|
| Existing classic OpenCV baseline | 4/14 | 0/10 | 26.07 | 0.06 |
| Existing ADE20K SegFormer baseline | 2/14 | 1/10 | 20.90 | 0.63 |
| Existing Astra low baseline | 12/14 | 8/10 | 13.04 | 3.2 |
| SmolVLM-500M, first short prompt, unconstrained | invalid 20/20 | invalid 20/20 | invalid | 4.280 |
| SmolVLM-500M, short prompt + JSON grammar | 3/14 | 0/10 | 28.70 | 6.053 |
| Qwen2.5-VL-3B, short prompt + JSON grammar | 5/14 | 1/10 | 23.93 | 6.195 |
| Qwen2.5-VL-3B, facade_fast prompt/schema + grammar | 5/14 | 1/10 | 24.42 | 14.531 |
| Grounding DINO tiny, four classes, 512 px, threshold .25 | 6/14 | 2/10 | 21.82 | 0.784 |
| DINO, windows/doors, 512 px, threshold .15 | 9/14 | 4/10 | 23.87 | 0.753 |
| DINO, windows/doors, 800 px, threshold .15 | **11/14** | 3/10 | 21.87 | 0.739 |
| DINO 800 + NMS/box-row mode ablation | 11/14 | 1/10 | 21.87 | +0.003 postprocess only |
| Florence-2-base-ft phrase grounding | 7/14 | 3/10 | 25.60 | 0.673 |
| CMP-finetuned SegFormer-B0, original projection peaks | 3/14 | 4/10 | 19.72 | 0.214 |
| CMP parser + metric peak spacing | 7/14 | 3/10 | 19.72 | 0.203 |
| **cv-models: DINO storeys + CMP columns/colour** | **11/14** | **4/10** | **19.72** | **1.196** |

The first SmolVLM prompt used slash-separated material alternatives. It returned those
alternatives literally, malformed JSON, or extra nested fields. The fallback scorer's
4/14 and 0/10 would merely repeat classic CV; they are **not SmolVLM scores**. The corrected
short prompt includes an example and LM Format Enforcer grammar. It produces valid JSON
but repeats the example's three storeys and four columns on all 20 walls. Grammar
constrains syntax, not visual truth. Both Qwen runs return valid schema fields on 20/20;
the full prompt and physical dimensions do not rescue counts.

DINO's first query (`a window. a door. a shop window. a balcony.`) misses many small windows.
The simpler `window. door.` query and .15 box/text thresholds improve recall. Masks shrink
each detected box by 15% on each side, reject boxes covering >20% of the image, and use the
existing projection/count logic. At 800 px the storey threshold is reached, but columns
still merge adjacent windows and miss repeated curtain-wall panes. Per-row duplicate
suppression (IoU .35) and modal box count made columns worse and is retained only as a
replayable research ablation, not used by the shipped engine.

CMP predicts explicit wall/window/door classes in a very small model. Mullions and trim
create multiple projection peaks; enforcing 2.4 m minimum floor and .9 m bay separation
improves storeys but sacrifices one column hit. Its masks still confuse shade, ornaments,
foliage, and glass. Many pseudo-labelled walls are tiny footprint segments or blank end
walls, not the clean whole facades in CMP. Geometry cannot make hidden window rows visible.

## Larger pseudo-label results

All rows below audit the 425-wall inventory; denominators are the 278 matched image inputs
and 241 eligible upper-column labels. The 147 fallbacks remain explicitly unmeasured.
Colour is agreement with Astra's daylight colour, not a new human colour reference.

| Variant | Storeys /278 | Columns /241 | Material /278 | Colour ΔE76 | Seconds/image |
|---|---:|---:|---:|---:|---:|
| CMP parser | 55 | 107 | 224 | 29.24 | 0.052 |
| CMP + metric spacing | 76 | 97 | 224 | 29.24 | 0.050 |
| **cv-models: DINO storeys + CMP columns/colour** | **139** | **114** | 224 | 29.24 | **0.991** |

The losing generative variants were screened out on the reviewed set and runtime; a full
278-image VLM run was not justified. Their larger-set results are **not measured**, not
assumed equal to the reviewed results. The final hybrid pass measures both component
models on every available direct reference.

## Material experiment

Frozen SigLIP-base uses three photographic prompt templates for each of seven material
phrases, including “painted plaster or stucco”, “exposed brick masonry”, and “bare concrete
panels”. Normalize embeddings, average each material's text embeddings, and choose maximal
cosine similarity. Zero-shot agreement is **121/278** and costs 0.035 s/image. On the original
20 walls it agrees with the existing rendered Astra material on 10/20; that is a pseudo
comparison, not a reviewer material score.

A ridge probe of the frozen 768-dimensional image features uses building-grouped splits,
with all 19 reviewed buildings excluded. SHA-256 buckets define train 158, validation 37,
test 40; 43 reviewed-building samples are excluded. Alpha ∈ {.01,.1,1,10,100} is chosen only
on validation. Alpha .01 yields 32/37 validation and **35/40 held-out test**, exactly the
**35/40 plaster-majority baseline** (zero-shot: 18/40). On the 20 reviewed images the probe
agrees on only 7/20. No evidence supports deploying this probe or interpreting its 87.5%
as broad material accuracy. Its weights and image embeddings remain local.

## Survey and model choice

Only publisher releases were downloaded, at immutable revisions. These sources describe
capabilities and availability; all facade scores above are our measurements.

- [Qwen2.5-VL-3B](https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct) supports visual
  localization/structured outputs. Tested FP16 on MPS with 512-pixel thumbnail and greedy
  grammar-constrained decoding; the 7B raw release does not fit this task's 10 GB cache cap.
- [SmolVLM-500M](https://huggingface.co/HuggingFaceTB/SmolVLM-500M-Instruct) is the small
  instruction VLM tested here. [InternVL2.5-1B](https://huggingface.co/OpenGVLab/InternVL2_5-1B)
  is another compact option, but was not downloaded or measured after the VLM trials.
- [Moondream2](https://huggingface.co/vikhyatk/moondream2) offers local image understanding;
  its additional weights/runtime were not tested. [PaliGemma 2](https://github.com/google-research/big_vision/tree/main/big_vision/configs/proj/paligemma)
  requires accepting Gemma terms and is oriented toward task finetuning; no gated download
  or new credentials were introduced. Neither is claimed to fail this benchmark.
- [Grounding DINO tiny](https://huggingface.co/IDEA-Research/grounding-dino-tiny) and
  [Florence-2](https://huggingface.co/microsoft/Florence-2-base-ft) were tested for openings.
  [OWLv2](https://huggingface.co/google/owlv2-base-patch16-ensemble) was surveyed, not run.
  [SAM 2](https://ai.meta.com/research/sam2/) refines prompted masks; it does not supply
  semantic window detections itself. We did not add it after detection recall/counts and
  the facade-trained semantic mask were still inadequate. Its possible colour benefit
  remains unmeasured.
- [CMP SegFormer-B0](https://huggingface.co/Marco333/segformer-b0-facade-cmp) provides actual
  published weights with explicit opening classes. The author's
  [mixed-data repository](https://huggingface.co/Marco333/segformer-b0-facade-mixed) contains
  a training script, not a released checkpoint. [DeepFacade](https://github.com/liuhantang/DeepFacade)
  exposes old PyTorch code without an obvious released checkpoint in the inspected tree;
  [GLNet](https://github.com/OctAne0113/GLNet) and
  [Precise](https://github.com/ikh-innovation/Precise) did not provide a directly usable
  published RGB parsing checkpoint in the inspected material. No claim of exhausting all
  CMP/ECP/eTRIMS/Graz checkpoints is made; no training run was substituted for missing weights.
- [SigLIP-base](https://huggingface.co/google/siglip-base-patch16-224) supplies the frozen
  material representation above. Its text/image similarity alone is not a calibrated
  cladding classifier for shaded panorama crops.

Florence initially failed under native Transformers 4.57: incompatible tokenizer image
fields, then unmatched weight names. Those attempts were discarded, not scored as model
quality. Publisher code with Transformers 4.49.0, eager attention and local-only loading
works. The exact separate-environment procedure is in the research scripts README.

## Downloads and reproducibility

Cache: `~/turku3d/tools/cv/cvr-models/`. Bytes include downloaded config/tokenizer files and
revision marker. Peak retained size is **9,507,829,842 bytes**, below 10,000,000,000. SmolVLM
was removed after testing before Qwen was downloaded. No weights, crops, crop derivatives,
reference contact sheets, embeddings, or pseudo labels enter the repository.

| Directory / original publisher | Revision | Bytes retained/downloaded |
|---|---|---:|
| cmp / Marco333/segformer-b0-facade-cmp | f95d623ab89d1179d9f59b8d390cc7a2818d255a | 14,890,579 |
| grounding-dino / IDEA-Research/grounding-dino-tiny | a2bb814dd30d776dcf7e30523b00659f4f141c71 | 690,305,586 |
| qwen / Qwen/Qwen2.5-VL-3B-Instruct | 66285546d2b821cf421d4f5eb2576359d3770cd3 | 7,520,892,473 |
| siglip / google/siglip-base-patch16-224 | 7fd15f0689c79d79e38b1c2e2e2370a7bf2761ed | 815,871,968 |
| florence / microsoft/Florence-2-base-ft | f6c1a25888ffc1d945ee8a1a77ac833c7303d46e | 465,869,236 |
| smolvlm / HuggingFaceTB/SmolVLM-500M-Instruct | a7da5b986cb59b408707209984f360a5f4ad7e47 | 1,019,883,697 downloaded, removed |

Versions: Python 3.12.14, torch 2.14.1, torchvision 0.29.1, transformers 4.57.1,
LM Format Enforcer 0.11.3, sentencepiece 0.2.1. Florence additionally pins transformers
4.49.0, tokenizers 0.21.4, timm 1.0.22 and einops 0.8.1. All inference is offline; preparation
is the only network action. Explicit CLI and benchmark commands are in
[textures/README.md](../../textures/README.md#local-facade-model-research) and
[the ablation README](../../tools/research/facade_models/README.md).

```sh
source ../sandbox.env
PY=textures/.venv/bin/python
FIXTURE="$HOME/turku3d/wt/textures-fast-facade-pass/work/textures/fast-measure"
$PY -m turkutex.facade_models_benchmark --engine grounding-dino-openings --size 800 \
  --fixture "$FIXTURE" --assembly "$TURKU3D_DATA/facade-approvals/assembly" \
  --output "$TURKU3D_WORK/reports/models/grounding-dino-800.json"
# Substitute cmp, cmp-grid, cv-models, grounding-dino, smolvlm --short, qwen [--short].
$PY -m pytest -q textures/tests/test_facade_cv.py textures/tests/test_facade_models.py
GATE_PYTHON="$PWD/textures/.venv/bin/python" scripts/dev/gate --fast --base main
```

Local detailed predictions/logs: `$TURKU3D_WORK/reports/models/`. Aggregate numbers are
[committed beside this report](facade-cv-models-metrics.json). The 20-wall model choices are exploratory and confidence
remains uncalibrated; do not auto-publish them or use a numeric confidence threshold to
claim equivalence to Astra. A useful next dataset would contain human counts and opening
boxes for broad, fully visible walls from new buildings, especially curtain walls and
shopfronts, rather than more propagated labels or narrow footprint slivers.
