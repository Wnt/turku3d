# W1-09h: generated facade rollout

Date: 2026-10-27. Sandbox: `w1-09h-facade-rollout` (Claude worker). Method and acceptance follow
[W1-09g](w1-09g-generated-office.md). Budget: at most 25 weekly quota points, stop at 52 %.
Raw accounting per batch: `~/turku3d/logs/facade-rollout/batch-NN.json`, running table:
`~/turku3d/logs/facade-rollout/ledger.md`. Contact sheets: [w1-09h-facade-rollout/](w1-09h-facade-rollout/).

## Settings

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
- [Contact sheet](w1-09h-facade-rollout/contact-batch-01.jpg).
- Stop rules: quota 28 % < 52 %, 0.1 points per accepted wall < 0.3, 10 of 23 readable walls
  (43 %) ≥ 40 %. Continue.
