# W1-09h: generated facade rollout

> Images derived from street-level photos (Google Street View, Apple Look Around, Mapillary) or
> Google 3D tiles were removed from the public repository on 2026-10-27; they remain in the local archive.

Date: 2026-10-27. Sandbox: `w1-09h-facade-rollout` (Claude worker). Method and acceptance follow
[W1-09g](w1-09g-generated-office.md). Budget: at most 25 weekly quota points, stop at 52 %.
Raw accounting per batch: `~/turku3d/logs/facade-rollout/batch-NN.json`, running table:
`~/turku3d/logs/facade-rollout/ledger.md`. Contact sheets: [w1-09h-facade-rollout/](w1-09h-facade-rollout/).

## Settings

- Every batch runs in its own sandbox and task (`w1-09h-facade-rollout`, `-b02`, …), because landing
  removes the sandbox. The queue, ledger and batch JSONs live in `~/turku3d/logs/facade-rollout/`.
- Generation `gpt-6-luna`/low, reviewer `gpt-6-astra`/low (routing `generated_visual_qa`), runner
  `--max-pct 52`. Batch 1 ran at `-j 8` as the validation batch.
- Acceptance: the automated audit (`facade_generated_qa`, which includes the reviewer answer) and my
  own look at every image next to its reference. At most one retry per wall with `generate --feedback`.
  Walls whose reference shows something other than the wall (a boat, pavement, a hoarding) are
  rejected without a retry.
- Published at 32 px/m (`min_pixels_per_metre: 32`) under the export lock into
  `$TURKU3D_DATA/facade-approvals/assembly/`. The inventory hash stays `ae9789…df2f`.

## Tooling

- **Reference screening and fallback** (`streetview/refs.py`, `building_jobs.screened_full_views`).
  Each rendered view records its pure-black fraction and a sharpness: the variance of the Laplacian
  divided by the intensity variance, at 512 px on the long side. A view is invalid above 20 % black
  or below 0.065 sharpness. Calibration on the W1-09g references: the motion-blurred Look Around views
  of `gis-103304663F_2` score 0.053 (full) and 0.019 (detail); the softest view of an accepted wall
  (`103295888D_20`, Google at 63°) scores 0.081, and accepted walls' primary views score 0.109–1.8.
  An absolute Laplacian variance did not separate them (the blurred view scored 216, a sharp one 88),
  because the blur streaks are high-contrast. When no rendered full view is valid, `refs` renders up to
  four more full views per source in the order Look Around → Google Street View → Mapillary and stops
  at the first valid one. Generate jobs use the valid full views in that order.
- **Wall queue** (`turkutex.facade_rollout queue`, `$TURKU3D_WORK/textures/rollout-queue.json`).
  813 street-facing walls of the approved inventory without a generated map (18 excluded): 50 core
  walls ≥ 4 m, 150 other core walls, then 50 m rings from the core bbox out to ring 5. The approved
  inventory is the buildings layer's `facades.geojson` (same hash), so no wall lies beyond it.
  **493 walls are deferred** because their aspect exceeds 3.5:1 either way; W1-09g measured 0 of 6
  layout-guided walls within the 3 % aspect check, and most of these are slivers under 2 m wide.
  That leaves 320 walls to batch: 44 + 24 in the core, 51, 75, 70, 46 and 10 in rings 1–5.

## Batches

| Batch | Walls | Readable | Accepted | Image calls | Reviewer calls | Quota before → after | Points/accepted | s/image call (mean) | Wall clock |
| ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| 1 (core ≥ 4 m, `-j 8`) | 25 | 23 | 10 | 36 | 35 | 27 → 28 | 0.10 | 47.8 | 12.6 min |
| 2 (rest of core, `-j 32`, aborted) | 23 | 13 | 0 | 13 (+20 failed with 429) | 13 | 28 → 28 | – | 26.1 | 12.1 min |
| 3 (core, reference grid, `-j 8`→`2`) | 33 | 12 | 5 | 20 (+17 failed with 429) | 19 + 33 grid | 28 → 30 | 0.40 | 25.5¹ | 16.9 min |

¹ The mean includes the 429 failures, which return within 2–10 s; successful image calls took 40–66 s.

### Batch 1

- Accepted (10): `103295900T_109`, `103295901U_23`, `103295901U_11`, `103295889E_23`,
  `1032958849_50`, `103295896N_17`, `103295901U_29`, `103304668M_31`, `103295896N_1`,
  `1032958849_82`. Five passed first time, five after one retry. All ten replaced photo-derived
  maps, so the bound count stays 341.
- Skipped (2): `gis-103295885A_30` and `gis-f4a3b5a9696950b36f9f22675c1994c00e878bb1_6` have no
  full-wall view in any source.
- Reference sources of the readable walls: 15 Look Around, 7 Google, 1 Mapillary. The fallback found
  a Google view for the two walls whose Look Around views were all black, but both "walls" are
  moored restaurant boats (`gis-3aa1c…_16`, `gis-103577006R_17`).
- Rejected without retry (6): two boats, three misregistered references that show pavement or an
  angled hoarding (`gis-103277347A_62`, `gis-103295886B_3`, `gis-103295886B_4`) and one steep
  oblique view of a neighbouring building (`103304667L_65`). The reviewer passed `gis-103295886B_4`
  as a blank wall; I rejected it because the reference is misregistered.
- Rejected after retry (7): the 3.17:1 guided wall `gis-103277347A_13` again missed the aspect;
  tall modern blocks lost or gained a storey (`103295901U_26`, `103295899S_368`); edge openings,
  a moved door or a gate (`103295899S_32`, `103295896N_30`, `103304659B_65`); a ground strip
  the reviewer still saw (`103304659B_67`).
- *Contact sheet (withheld)*.
- Stop rules: quota 28 % < 52 %, 0.1 points per accepted wall < 0.3, 10 of 23 readable walls
  (43 %) ≥ 40 %. Continue.

### Batch 2 (aborted)

- The orchestrator asked for the remaining 43 core walls as one batch at `-j 32`. 20 of 33 generate
  calls failed with HTTP 429 ("exceeded retry limit") within 30 s and made no image; those walls
  went back to the queue (batch 3). `-j 8` had no 429 in batch 1.
- 10 walls have no valid reference: 7 have no full-wall view, `kanta-c0358…_3` has only 100 %
  black Look Around crops, and `1032958849_12` / `_15` have only blurred views (sharpness
  0.003–0.062).
- None of the 13 images was accepted. Nine references show pavement, water and boats, yard
  machinery, a railing, parked cars, a glass fence or a construction sign instead of the wall,
  and the model invented a facade for each. The audit passed `103304670P_39`; I rejected it
  because its gable is drawn against a white canvas that would be textured onto the rectangular
  wall. The other three are plausible but tree-occluded or show sky and street strips.
- *Contact sheet (withheld)*.

**Change before batch 3.** Wrong-content references cost 9 of 13 images here and 6 of 23 in batch 1.
They pass the black and blur screen, and the reviewer only notices them after an image has been
paid for. A new `refgrid` stage asks the fixed reviewer (astra/low, no image) whether the rectified
references show the outlined wall at all. When they do, it records the wall's storeys, window
columns, ground-floor openings, plinth, cornice and cladding. `generate` skips walls whose
references do not show them. For the others it passes the measured grid and the exact aspect to
the image model as hard constraints, also in `--feedback` retries. This is the orchestrator's
grid-in-prompt request, plus a gate.

### Batch 3 (reference grid)

- Walls: the 20 walls hit by 429 in batch 2 plus the 13 walls rejected there. For those 13 this was
  their one retry, now with the reference-grid prompt. This completes the office core.
- `refgrid` (33 astra/low calls, 73 s at `-j 8`) found the wall in **12 of 33** walls. The 21 it
  rejected show pavement, terraces and boats, construction fences and site equipment, foliage, or are
  too dark. That matches my look at the batch-2 images: it removed every wrong-content reference
  without paying for an image. Its `problem` and `cladding` strings came back in Finnish, the
  account's default language. The prompt now asks for English; the counts were unaffected.
- 429 limits persisted after the 33-call reference burst: 5 of 12 first-pass calls failed at `-j 8`,
  3 of 7 retries at `-j 4`, and 1 of 3 at `-j 2`, which then succeeded on the runner's own retry.
  Halving and continuing worked, as instructed.
- Accepted (5): `103304668M_16` (blank brick), `gis-103295891H_4`, `103304670P_39` (second image,
  the gable now in continuous brickwork), `gis-103277347A_57` and `103295889E_22` (both after a
  retry). Published; bound 341 → 344, because three of them were unbound before.
- The audit passed `103304670P_37` after its retry; I rejected it because its spire stands against
  white canvas that would be textured onto the rectangular wall. That is the same failure as
  `103304670P_39` in batch 2. Pavement or sky strips, grouped windows drawn as even ones and a
  moved door rejected the rest.
- *Contact sheet (withheld)*.
- Stop rules: quota 30 % < 52 %. Over the last two batches, 2 points for 5 accepted walls is
  **0.40 points per accepted wall > 0.3**: stop. Acceptance was 5 of 12 readable walls (42 %),
  below the orchestrator's 45 % bar for continuing. The rule cannot separate the reference-grid
  calls from the image calls: 33 astra calls ran before any image in this batch.

## Outcome (stopped after batch 3)

The stop rule triggered after batch 3, and the orchestrator stopped the rollout. Every one of the
68 batchable office-core walls has been attempted, but no ring wall has. Quota went 27 → 30 %
(3 of the 25 approved points).

### Results so far

- **Published:** 15 walls, 18 → 33 generated maps in the approval root.
- **Core coverage:** 33 of 218 street-facing core walls now have a generated map. That is 33 of
  the 86 core walls the queue does not defer as extreme aspect. The 132 deferred core walls
  (beyond 3.5:1) got no image calls.
- **Contact sheets:** each shows the reference crop next to the generated image with the verdict
  for every wall, skipped walls included.
  - *batch 1 (withheld)*
  - *batch 2 (withheld)*
  - *batch 3 (withheld)*
  - A skipped wall with no rendered full or partial view at all (for example `gis-103295885A_30`)
    has no crop to show.

Final outcome per unique core wall (68; the 13 walls of batch 2 count once, with their batch-3 result):

| Outcome | Walls | Width m, median (range) | Height m, median (range) | Aspect, median (range) | < 4 m wide | `gis-`/`kanta-` IDs |
| --- | ---: | --- | --- | --- | ---: | ---: |
| accepted | 15 | 8.2 (1.6–18.9) | 10.9 (3.9–20.6) | 0.64 (0.33–2.81) | 3 | 2 |
| rejected after image(s) | 20 | 8.1 (3.5–32.5) | 11.1 (6.3–21.5) | 0.65 (0.33–3.17) | 2 | 8 |
| no usable reference | 33 | 3.0 (1.0–12.9) | 6.3 (3.0–20.4) | 0.47 (0.31–1.85) | 19 | 23 |

Size does not separate accepted from rejected walls. The reference does: walls without a usable
reference are small, and most of them carry the GIS-derived IDs of riverside kiosks and boats,
yards and construction sites.

### Prompts per batch

- **Batches 1 and 2** used the same template, `textures/prompts/facade_generate.md` at `0ed329c`.
  Its target line is `Target wall: $width_m metres wide, $height_m metres high, aspect $aspect.
  $bay_instruction`, followed by the reference list, the drawing rules, the layout and retry
  instructions, the albedo rules and the style fragment.
- **Batch 2 did not use the grid prompt.** It ran the batch-1 prompt at `-j 32`, as the
  orchestrator asked before the grid request arrived.
- **Batch 3** used `8e246c1`, which changes two things:
  - The target line states the aspect as a hard rule: `The finished wall rectangle must have
    exactly this width:height aspect: $aspect.`
  - A new `$grid_instruction` line comes from the `refgrid` answer. For `103295889E_22` it reads:
    `Measured from the references before drawing; these are hard constraints: 1 storeys including
    the ground floor; no upper window rows; 2 ground-floor openings (doors, shop windows, gates);
    a distinct plinth band; a distinct cornice or eaves band; cladding: Vaaleankeltainen
    pystylaudoitus.` The cladding came back in Finnish; the prompt now asks for English.
  - `--feedback` retries keep both lines and add `A previous image of this wall was rejected in
    review: <reason> Correct that while following every rule below.`
- **The model's own image prompt.** Luna rewrites the job prompt into the `image_gen` prompt
  (recorded in `run_generate/attempts.jsonl`). In batch 3 that rewrite carries the counts through,
  for example: `one storey total, no upper window row; exactly two ground-floor openings … Target
  wall rectangle aspect ratio must be exactly 0.6217:1`. Its retry also adds opening positions as
  percentages of the wall.

### Reference gates and their rejections

- **Pixel screen** (all batches, before any call):
  - more than 20 % pure black, or
  - contrast-normalised Laplacian sharpness below 0.065 at 512 px.

  Without a valid full view, refs falls back from Look Around to Google to Mapillary (four views per
  source). Walls skipped this way:
  - batch 1: 2 with no full-wall view in any source;
  - batch 2: 7 with no full-wall view, 1 with only black views, 2 with only blurred views.

  The fallback found a valid view for 2 walls in batch 1 and 4 in batch 3.
- **Reference grid** (batch 3 only). This is a yes/no judgement by the astra/low reviewer
  (`wall_visible`), with no numeric threshold. It rejected 21 of 33 walls:

  | Reason (translated) | Walls |
  | --- | ---: |
  | pavement, asphalt or paving only | 6 (`gis-1032581214_26`, `_28`, `_31`, `_33`, `gis-103929947X_6`, `_7`) |
  | terrace, boat or water | 5 (`gis-3aa1c…_18`, `_20`, `_21`, `kanta-84488…_2`, `gis-103577006R_16`) |
  | construction fence, canopy or site equipment | 5 (`gis-103295886B_1`, `gis-f4a3b…_4`, `1032958849_21`, `_27`, `gis-103929947X_8`) |
  | foliage | 2 (`103295901U_8`, `103304659B_64`) |
  | too dark to count openings | 2 (`103304659B_62`, `103295901U_19`) |
  | fence and vehicle | 1 (`103304668M_25`) |

### Why batch 2 accepted nothing

Batch 2 had no grid in its prompt; the grid arrived in batch 3. My hypothesis is that batch 2
failed on its references, not its prompt:

- It drew the leftover core walls after the best-referenced ones were taken in batch 1. They are
  smaller, and many are riverside or construction-site walls whose rectified crops land on
  pavement, boats, fences or cars (median 3 m wide among the unusable ones).
- Nine of its 13 images were facades invented from such crops, and the reviewer correctly rejected
  every one.
- The 429 failures removed another 20 walls before any image.

Batch 3 supports this hypothesis. Once `refgrid` removed the 21 invisible walls, 5 of 12 were
accepted: 3 on the first image and 2 after a retry. That is about batch 1's rate (10 of 23), on the
walls batch 2 had failed on.

### Cost per accepted wall

The stop rule judges quota points per accepted wall over the last two batches: 2 points for
5 walls. Batch 3 includes 33 reference-grid calls (three images each) that the integer quota
reading cannot separate from its 20 image calls and 19 reviews. Batch 1 cost under 1 point for
10 walls with 36 image calls.

### Street view (batch 1, both backends)

The renders use the reference camera of each wall, before (`cc7903a`, photo-derived maps) and
after (`8e246c1`, with batch 1):

- *WebGPU (withheld)*
- *WebGL2 (withheld)*

`103295896N_1` changes from a smeared photo to a crisp pink facade with its niche statue.
`103295901U_29` becomes a sharp modernist elevation.

In the same view, the neighbouring wall `103295901U_26` keeps its binding but looks blurrier after.
It shares tile `t0_0_1` with at least three of the new 32 px/m maps (`103295901U_11`, `_23` and `_29`, the same building). That is consistent with the packager
lowering other walls' density to stay under the 8 MB cap. Its photo approval has since been
retired by `tool-public-release`, so it will fall back to library material.

The batch-3 maps reach the live game with the next integration, so no after-sheet exists yet for
them; batch 2 published nothing. Integrations `0ed329c` and `8e246c1` were green with facades
bound 341. The visual GLBs of `t0_0_0` and `t0_0_1` grew from 4.99 to 5.38 MB and from 4.76 to
4.98 MB.
