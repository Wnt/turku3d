# W1-09g: generated office-core facades, measured per Codex model

> Images derived from street-level photos (Google Street View, Apple Look Around, Mapillary) or
> Google 3D tiles were removed from the public repository on 2026-10-27; they remain in the local archive.

Date: 2026-10-26. Sandbox: `w1-09g-generated-office` (Claude worker). Pilot: [w1-09f](w1-09f-generated-facades.md).
Raw measurements: `~/turku3d/logs/facade-bench/batch-{1,2,3}.json` (per call: model, effort, seconds,
tokens, quota before/after; per wall: calls, automated problems, reviewer answer, manual verdict).

## Method

- **Walls.** From `APPROVED_INVENTORY.geojson`: `street_facing`, centroid in E 23459562.5–23459812.5,
  N 6704125–6704375, width ≥ 4 m (68 walls), minus the three pilot walls. Ranked by width divided by
  distance to the player spawn (E 23459673.3, N 6704247.5). The first 24 with a full-wall reference were
  split into three batches of eight. Each batch got a similar mix of near-spawn timber walls, 20 m stone
  blocks, small walls and extreme-aspect (> 3:1, layout-guide) walls. Total wall area was 1,348 / 1,503 / 1,372 m².
  Two batch-1 walls turned out to have unusable Look Around references: black or motion-blurred.
  Luna therefore also generated the next two ranked walls (`103295899S_368`, `1032958849_50`).
  The same screening replaced two batch-3 walls before their run, `gis-103295886B_4` with
  `1032958849_47` and `gis-103577006R_17` with `103277348B_344`, so each model saw eight walls with
  readable references.
- **Models.** Batch 1 `gpt-6-luna`/low, batch 2 `gpt-6-sol`/medium, batch 3 `gpt-6-astra`/medium.
  `TURKU3D_CODEX_MODEL`/`TURKU3D_CODEX_EFFORT` were set only for `facade-batch generate`, with `-j 4 --max-pct 60`.
  No other Codex job ran: zero `codex exec` processes before every batch and no running `codex-task`.
- **Acceptance.** A wall is accepted only if three checks agree:
  1. `turkutex.facade_generated_qa`: aspect within 3% of the content box, equal texel density, intact KTX2
     and source hashes, and a reviewer answer for the exact reviewed bytes.
  2. The reviewer's answer: storeys, upper openings, grid, orthographic, clean.
  3. My own look at every image next to its reference.

  At most one retry per wall: two image calls in total.
- **Reviewer.** A fixed reviewer, `gpt-6-astra`/low, is used for every batch (routing kind `generated_visual_qa`),
  so the reviewer cannot favour its own model. Batch 1 first used `gpt-6-luna` as its own reviewer.
  That reviewer contradicted itself: for `103295900T_109` it reported 7 expected openings in one call and
  6 in the next, and it rejected a correct four-bay retry of `103295893K_42` as an "extra storey".
  Its 15 calls are recorded under `extra_reviewer_runs` in `batch-1.json` and were not used for acceptance.

## Results per model

| | gpt-6-luna / low | gpt-6-sol / medium | gpt-6-astra / medium |
| --- | ---: | ---: | ---: |
| Walls attempted (readable references) | 10 (8) | 8 (8) | 8 (8) |
| Walls accepted | **5** | **5** | **5** |
| Image (generate) calls | 17 (15 on readable walls) | 14 | 14 |
| Image calls per accepted wall | 3.0 on readable walls | 2.8 | 2.8 |
| First images rejected and retried | 7 | 6 | 6 |
| Reviewer calls (astra/low) | 12 (+15 superseded luna) | 14 | 14 |
| Generate input tokens, uncached / cached | 282,223 / 633,728 | 232,559 / 1,046,144 | 260,689 / 932,608 |
| Generate output (reasoning) tokens | 11,028 (0) | 15,705 (6,207) | 10,544 (93) |
| Reviewer input tokens, uncached | 250,112 | 236,356 | 186,528 |
| Seconds per image call, mean (min–max) | **44.4** (29–59) | 57.5 (36–68) | 78.2 (53–104) |
| Batch wall clock incl. retries and post | 11.5 min | 7.9 min | 8.7 min |
| Weekly quota before → after | 25 → 25 | 25 → 26 | 26 → 27 |
| Automated QA pass rate (final images) | 5/10 | 5/8 | 5/8 |
| My visual quality rank | 2 | 3 | 1 |

The weekly quota is reported only in whole percent, and the rollout float read 25.0 throughout.
All three batches together moved the quota from 25% to 27%: at least 1 and at most 3 points for 45
image calls and 55 reviewer calls. Per batch, quota cannot separate the models. Luna spent less than
one point on 10 walls; sol and astra each crossed one integer boundary. No batch came near the stop
rules: the quota peaked at 27% against the 45% limit, every batch stayed below 0.2 points per accepted
wall against the 2-point limit, and every model got 5 of its 8 readable walls accepted.

## Failure modes and visual ranking

[Contact sheets](w1-09g-generated-office/): `contact-gpt-6-luna.jpg`, `contact-gpt-6-sol.jpg` and
`contact-gpt-6-astra.jpg` show the reference beside the final image with the verdict for each wall.

- **gpt-6-luna** (rank 2). First images kept the photographed colours. Their mistakes were structural:
  - a bay hidden behind scaffolding was dropped (`103295893K_42`);
  - an 11% aspect error and 6 instead of 7 columns (`103295900T_109`);
  - simplified ground floors (`103295900T_30`, `1032958849_31`).

  One retry fixed four of the five. Edges and joinery are crisp, with slightly less material depth than
  astra. It ignored the 7.3:1 layout guide on `gis-103304663F_2`, a wall whose reference was unusable anyway.
- **gpt-6-sol** (rank 3). First images had colour errors: yellow for greyish-brown (`gis-103295891H_0`)
  and for cream (`103295900T_114`). It also added a roof-tile strip (`103295888D_20`) and a pavement strip.
  It missed both layout-guide aspects, by 18% then 7% (`103304668M_2`) and by 4.6% (`gis-103277347A_13`).
  Retries fixed colours and strips, never the guide aspect.
- **gpt-6-astra** (rank 1). It had the richest material detail: weathered timber, rusticated stone,
  pediments (`103295893K_38`, `103277348B_344`, `103295896N_8`). Its first images were often in the wrong
  colour family: beige for brown timber, red for cream and pink. It also missed both layout-guide aspects
  (`103304668M_40` 3.35 vs 3.83, `gis-103304662E_16` 3.56 vs 3.73), even after retry.
- **All models.** None hit a layout-guide rectangle within 3% for walls wider than about 3.5:1 (0 of 6 attempts).
  None reconstructed six storeys behind a tree (`103295901U_11`, `103295901U_23`).
  Reference failures dominate the rest. Some Look Around rectified views are 54–100% pure black (outside
  panorama coverage) yet score highly. Generate jobs now skip any view that is more than 20% pure black.

## Recommendation and extrapolation

Use **gpt-6-luna / low for generation, with the fixed gpt-6-astra / low reviewer and a manual look**:

- **Acceptance.** It accepted as many walls as the other two models (5 of 8 readable).
- **Errors.** Its first-image errors were structural and fixable with a short retry.
  Sol and astra errors were colour errors, which are more visible in the street.
- **Speed.** It was the fastest model: 44 s per image versus 58 s and 78 s.
- **Quota.** It spent the least: less than one quota point for 17 image calls, with no reasoning tokens.

Astra's final images are slightly richer but cost 1.8× the time per call and did not raise the acceptance rate.

**Remaining work.** The office core has 218 street-facing walls, 68 of them ≥ 4 m. Eighteen of those
68 now have generated maps: 3 pilot walls and the 15 published here. That leaves 50 walls ≥ 4 m and
200 street-facing walls in total.

**Cost.** Measured here: 1.7 image calls and 1.2 reviewer calls per attempted wall, and 0.02–0.07 quota
points per image call including its review. The quota range follows from the integer quota readings.

- 50 walls ≥ 4 m: about 85 image calls and 60 reviewer calls, **2–6 weekly quota points**, about 25 min at `-j 4`.
- All 200 walls: about 340 image calls and 240 reviewer calls, **7–23 quota points**, about 1.5 h.

**Yield.** Expect about 60% of walls with readable references to be accepted. Expect 10–20% of walls
to have no readable reference: all views black, blurred or hidden. Walls below 4 m are often fragments,
where the photo-derived map may be good enough.

**Before the extreme-aspect walls.** Walls wider than 3.5:1 should not get image calls until the layout
guide is fixed. Options are generating such walls in sections or post-cropping to the measured aspect.

## Publication and build

**Published maps.** The 15 accepted map sets (albedo, normal and ORM KTX2 plus JSON sidecar) were
staged at 32 pixels/metre with `min_pixels_per_metre: 32`. They were published under the export lock to
`$TURKU3D_DATA/facade-approvals/assembly/facades/`:

- luna: `103295893K_34`, `103295893K_42`, `1032958849_31`, `103295900T_13`, `103295900T_30`
- sol: `103295888D_20`, `103295889E_21`, `103295899S_341`, `gis-103295891H_0`, `103295900T_114`
- astra: `103295893K_38`, `1032958849_47`, `103295896N_8`, `103277348B_344`, `1032958849_65`

Fourteen of them replace photo-derived maps. `gis-103295891H_0` was previously unbound.

**Manifest.** The inventory hash is unchanged (`ae9789…df2f`). Bound IDs went from 340 to 341, so there is no facade regression.

**Sandbox build.** The four-tile build used a clone of that root in `$TURKU3D_WORK/textures/assembly` and bound
341 facades. Visual tile wire bytes, sandbox versus the live release:

| Tile | Live release | Sandbox |
| --- | ---: | ---: |
| `t0_0_0` | 7,166,577 | 7,187,416 |
| `t0_0_1` | 7,540,870 | 7,500,213 |
| `t0_1_0` | 7,456,426 | 7,463,804 |
| `t0_1_1` | 7,599,708 | 7,599,708 |

All four tiles stay under the 8 MB cap.

**Street views.** The before/after sheets render the same camera pose, aimed at each wall from its best
reference camera:

- left: live release `9418f24d…` with photo-derived maps;
- right: the sandbox build with generated maps.

Each sheet shows two walls per model:

- *WebGPU street before/after (withheld)*
- *WebGL2 street before/after (withheld)*

The generated maps are sharper and free of photo smear: `103295900T_30`, `103277348B_344` and the
ground floors. They also remove the scaffolding and hoarding baked into the photo maps. On walls whose
photo map was already clean (`103295893K_42`), the change is small.

Raw renders and metadata for all 26 views are in `$TURKU3D_WORK/reports/w1-09g/{before,after}-{webgpu,webgl}/`. Only the WebGL2 after-render of `1032958849_47` is missing; it timed out under host load above 280.

## Verification

```sh
source ../sandbox.env
B="$TURKU3D_WORK/textures/bench/batch-1"   # likewise batch-2, batch-3
TURKU3D_CODEX_MODEL=gpt-6-luna TURKU3D_CODEX_EFFORT=low textures/.venv/bin/python -m turkutex facade-batch generate "$B/facades.geojson" --run "$B" --refs "$TURKU3D_WORK/textures/refs" --output "$B/staging" -j 4 --max-pct 60
textures/.venv/bin/python -m turkutex facade-batch visualqa "$B/facades.geojson" --run "$B" --refs "$TURKU3D_WORK/textures/refs" --output "$B/staging" -j 4 --max-pct 60
TURKU3D_CODEX_MODEL=gpt-6-luna TURKU3D_CODEX_EFFORT=low textures/.venv/bin/python -m turkutex facade-batch generate "$B/facades.geojson" --run "$B" --refs "$TURKU3D_WORK/textures/refs" --output "$B/staging" -j 4 --max-pct 60 --feedback "$B/feedback.json"
textures/.venv/bin/python -m turkutex facade-batch post "$B/facades.geojson" --mode generate --run "$B" --refs "$TURKU3D_WORK/textures/refs" --output "$B/staging" --ppm 32
textures/.venv/bin/python -m turkutex.facade_generated_qa --facades "$B/facades.geojson" --refs "$TURKU3D_WORK/textures/refs" --generated "$B/generated" --staging "$B/staging" --visual-qa "$B/run_visualqa/out.jsonl" --output "$B/acceptance.json" --min-ppm 32
make tile-build T='t0_0_0 t0_0_1 t0_1_0 t0_1_1'
```
