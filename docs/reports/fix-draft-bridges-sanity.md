# Draft bridge and barrier sanity check, 2026-10-28

The six Aura playtest spots and the added Hämeensilta check now have one deck per physical crossing,
short mapped boat access landings, and no generated untagged above-grade quay retaining walls.
All 21 real tiles build. The original generators emit 138 complete decks across those tile requests;
the corrected generators emit 31 clipped fragments representing 9 physical crossings and 19 short
access landings. A crossing at a tile boundary has multiple fragments, not multiple complete decks.

## Causes and corrections

| Spot | Confirmed cause | Correction and source check |
|---|---|---|
| 1. Teatterisilta | One OSM way was emitted in four neighbouring tiles. Each tile clamped ground samples to its own grid, giving different heights and 105–124 m extended decks. | Outline 529921265 contains way 105064411: one 66.35 m span plus 3 m blending at each end, split into two tile fragments with identical 3.357–3.537 m profiles. This particular stack was tile duplication, not multiple carriageways. |
| 2. Theatre boats | Short bridge-tagged access ways received road-bridge clearance and long approaches; ship footprints were not obstacles. | Pier-connected ways and short steps/path links retain their mapped span and endpoint levels, receive no clearance lift or piers, and are cut at ship/building footprints. Examples: 777814622, 777814623, 777835958, 777835959. |
| 3. River bank | Quay retaining-wall records became default 1.8 m free-standing walls; duplicated OSM line/area records and unexcluded water added geometry. | Deduplicate records, exclude water and footprints, suppress unmeasured above-grade retaining walls, and use 0.5 m only for explicitly tagged parapets. Bare banks never generate a barrier. |
| 4. Myllysilta | Two ways (206891995, 531029806) each emitted in four tile requests. | Outline 531029803 gives one 94.19 m crossing plus 6 m total blending, emitted in one tile. Parallel same-level ways share its profile. |
| 5. Boat slabs / Martinsilta | Martinsilta's three transport ways and neighbouring short boat accesses were independently extended and repeated. | Outline 531032245 merges 4306342, 42753379 and 206892148; two clipped fragments share one profile. Access 719629409 remains 13.94 m and is cut at obstacles. |
| 6. Betanianpolku near Myllytunneli | Long whole-way ramps and tile duplication produced 111.68 m and 124.89 m decks. | Outline 1039866614 / member 22568810 bounds the 32.37 m span, with only 6 m total blending: 38.37 m, one tile. |
| 7. Hämeensilta | Eight transport ways belonged to one branched outline. A rectangular outline axis followed an approach branch; lidar sampled on the upper deck was mistaken for railway ground. | Outline 525854016 merges 4075084, 4646622, 27820864, 32916632, 57838444, 631256462, 631256465 and 631256467. The carriageway defines the axis, rails follow the real outline, branches blend to ground. Underpass ground is interpolated from samples outside the deck footprint (railway approximately 16.3–16.6 m, not deck 25.2 m). Final profile including blends: 24.730–25.839 m. |

Clearance now requires an interior transverse crossing of a lower way, excluding bridge members,
parallel ways and connecting endpoints. Same-layer non-bridge roads/paths can pass below an explicitly
bridge-tagged way; equal/higher-layer bridges cannot lift it. Full raw ground blocks make each crossing's
profile independent of the requesting tile. Actual mapped pier polygons are used to identify access
landings; the old generator did not directly turn every `man_made=pier` polygon into a bridge. Boat-access
bridge tags and exaggerated ramps were the confirmed source of the boat slabs.

The neighbouring-tile build also exposed two tunnel failures: reversing mitered wall offsets at tight
corners (`t0_-2_1`, `t0_4_-3`) and a microscopic building-passage footprint cut that collapsed in float32
collision coordinates (`t0_4_-2`). Segment wall prisms and sub-0.5 mm footprint simplification repair
these at their source. Bridge and tunnel visuals/collision are clipped to their actual tile bounds.

## Real-data coverage and evidence

Aura: i = -3…0, j = 1…3 (12 tiles). Hämeensilta: i = 4…6, j = -4…-2 (9 tiles), including all eight
neighbours of the reported HUD tile `t0_5_-3`; the main bridge footprint itself lies in `t0_6_-2`.
All 21 after tiles were inspected from top-down and oblique views, together with both views of all seven
spots. The local gallery contains 106 PNGs (50 before, 56 after), paired by camera:

`$TURKU3D_WORK/reports/bridge-sanity/index.html`

The raw source/output records are `before/decks-{aura,hame}.json` and `after/decks-{aura,hame}.json`.
They preserve crossing IDs, member IDs, spans, underpass IDs, profile ranges and barrier counts/metres.
Repeated after crossing IDs have identical profile ranges. Source comparison uses cached OSM, city
building/water footprints, orthophotos and lidar; no street imagery or raw city data is committed.
The separate calibrated Google 3D comparison tool was unavailable (its owner reported headless 2D
fallback and missing altitude calibration), so it was not used as evidence.

The baseline is the pre-fix coarse code at `7f0946a9`. Eighteen baseline tiles build; the three failures
above have explicit missing-baseline panels in the gallery. Counts below include generator output for
all 21 tiles, including those whose baseline packaging subsequently failed.

## Per-tile OSM and deck counts

Before counts are selected source ways / complete deck emissions, including the old 100 m margin.
After source ways are the members of decks actually emitted in that tile; fragments are clipped to the
tile. A source way or crossing touching two tiles is counted in both rows. Across rows there are 47
unique before source ways, versus 42 contributing after source ways and 28 resolved object IDs.

| Tile | OSM member ways before → after | Deck emissions before → after | After crossing fragments | After landing fragments | Barrier segments before → after |
|---|---:|---:|---:|---:|---:|
| `t0_-3_1` | 1 → 0 | 1 → 0 | 0 | 0 | 260 → 260 |
| `t0_-3_2` | 7 → 4 | 7 → 2 | 1 | 1 | 260 → 157 |
| `t0_-3_3` | 7 → 3 | 7 → 3 | 0 | 3 | 164 → 101 |
| `t0_-2_1` | 3 → 0 | 3 → 0 | 0 | 0 | 201 → 194 |
| `t0_-2_2` | 8 → 6 | 8 → 3 | 2 | 1 | 362 → 77 |
| `t0_-2_3` | 6 → 1 | 6 → 1 | 1 | 0 | 288 → 263 |
| `t0_-1_1` | 9 → 5 | 9 → 5 | 1 | 4 | 415 → 216 |
| `t0_-1_2` | 8 → 0 | 8 → 0 | 0 | 0 | 283 → 148 |
| `t0_-1_3` | 1 → 0 | 1 → 0 | 0 | 0 | 71 → 70 |
| `t0_0_1` | 10 → 5 | 10 → 5 | 1 | 4 | 570 → 300 |
| `t0_0_2` | 4 → 0 | 4 → 0 | 0 | 0 | 615 → 434 |
| `t0_0_3` | 0 → 0 | 0 → 0 | 0 | 0 | 219 → 187 |
| `t0_4_-4` | 9 → 0 | 9 → 0 | 0 | 0 | 400 → 400 |
| `t0_4_-3` | 6 → 4 | 6 → 4 | 0 | 4 | 343 → 156 |
| `t0_4_-2` | 3 → 0 | 3 → 0 | 0 | 0 | 486 → 152 |
| `t0_5_-4` | 7 → 4 | 7 → 3 | 3 | 0 | 231 → 204 |
| `t0_5_-3` | 14 → 0 | 14 → 0 | 0 | 0 | 381 → 337 |
| `t0_5_-2` | 11 → 2 | 11 → 2 | 0 | 2 | 191 → 90 |
| `t0_6_-4` | 0 → 0 | 0 → 0 | 0 | 0 | 539 → 220 |
| `t0_6_-3` | 12 → 4 | 12 → 1 | 1 | 0 | 201 → 194 |
| `t0_6_-2` | 12 → 12 | 12 → 2 | 2 | 0 | 442 → 224 |

Barrier segments total **6,922 → 4,384**. The remaining crossings outside the four Aura spans are
Hämeensilta (525854016), Hämeenramppi (663485978), Rantarata (517881744), and two distinct Helsinginkatu
outlines (1091730697, 1091730698). These remain separate physical structures.

## Per-tile barriers by source type

Each cell is **source records / emitted segments before → after**. Source records are selected OSM
features, not expected render draws: one line creates many at-most-2.5 m segments. Deduplication and
obstacle filtering can reduce the source record count; water/road clipping and retaining-wall semantics
reduce output. Gates deliberately emit zero solid barrier segments.

| Tile | `fence` | `wall` | `retaining_wall` | `hedge` |
|---|---:|---:|---:|---:|
| `t0_-3_1` | 7/105 → 7/105 | 5/115 → 5/115 | 0/0 → 0/0 | 4/40 → 4/40 |
| `t0_-3_2` | 17/89 → 15/89 | 6/27 → 6/27 | 7/103 → 7/0 | 5/41 → 5/41 |
| `t0_-3_3` | 9/51 → 9/51 | 1/4 → 1/4 | 4/63 → 4/0 | 6/45 → 6/45 |
| `t0_-2_1` | 19/184 → 19/184 | 3/10 → 3/10 | 2/7 → 2/0 | 1/0 → 1/0 |
| `t0_-2_2` | 12/102 → 8/70 | 4/7 → 4/7 | 21/253 → 21/0 | 1/0 → 1/0 |
| `t0_-2_3` | 22/231 → 22/222 | 4/0 → 4/0 | 4/16 → 4/0 | 4/41 → 4/41 |
| `t0_-1_1` | 15/141 → 13/136 | 7/63 → 7/63 | 6/194 → 6/0 | 7/17 → 7/17 |
| `t0_-1_2` | 6/136 → 6/134 | 2/14 → 2/14 | 10/133 → 10/0 | 0/0 → 0/0 |
| `t0_-1_3` | 4/41 → 4/40 | 0/0 → 0/0 | 0/0 → 0/0 | 1/30 → 1/30 |
| `t0_0_1` | 14/274 → 12/264 | 3/9 → 3/9 | 9/260 → 9/0 | 10/27 → 10/27 |
| `t0_0_2` | 8/440 → 8/428 | 1/6 → 1/6 | 9/169 → 9/0 | 0/0 → 0/0 |
| `t0_0_3` | 10/135 → 10/134 | 3/14 → 3/14 | 3/31 → 3/0 | 4/39 → 4/39 |
| `t0_4_-4` | 6/130 → 6/130 | 20/190 → 20/190 | 0/0 → 0/0 | 6/80 → 6/80 |
| `t0_4_-3` | 13/57 → 13/57 | 7/97 → 7/97 | 30/187 → 30/0 | 0/0 → 0/0 |
| `t0_4_-2` | 11/73 → 11/71 | 9/37 → 8/30 | 37/327 → 37/2 | 3/43 → 3/43 |
| `t0_5_-4` | 5/129 → 3/112 | 2/47 → 2/47 | 2/10 → 2/0 | 2/45 → 2/45 |
| `t0_5_-3` | 13/158 → 13/152 | 4/132 → 4/128 | 4/34 → 4/0 | 3/57 → 3/57 |
| `t0_5_-2` | 7/80 → 7/79 | 1/11 → 1/11 | 6/100 → 5/0 | 0/0 → 0/0 |
| `t0_6_-4` | 10/138 → 10/138 | 1/1 → 1/1 | 11/321 → 11/2 | 3/53 → 3/53 |
| `t0_6_-3` | 12/174 → 12/174 | 1/20 → 1/20 | 1/7 → 1/0 | 0/0 → 0/0 |
| `t0_6_-2` | 8/61 → 6/46 | 11/134 → 11/133 | 9/206 → 8/11 | 3/27 → 3/27 |

| Tile | `gate` | `guard_rail` | `handrail` | `bollard` |
|---|---:|---:|---:|---:|
| `t0_-3_1` | 3/0 → 3/0 | 0/0 → 0/0 | 0/0 → 0/0 | 0/0 → 0/0 |
| `t0_-3_2` | 6/0 → 6/0 | 0/0 → 0/0 | 0/0 → 0/0 | 0/0 → 0/0 |
| `t0_-3_3` | 0/0 → 0/0 | 0/0 → 0/0 | 0/0 → 0/0 | 1/1 → 1/1 |
| `t0_-2_1` | 12/0 → 11/0 | 0/0 → 0/0 | 0/0 → 0/0 | 0/0 → 0/0 |
| `t0_-2_2` | 2/0 → 2/0 | 0/0 → 0/0 | 0/0 → 0/0 | 0/0 → 0/0 |
| `t0_-2_3` | 3/0 → 2/0 | 0/0 → 0/0 | 0/0 → 0/0 | 0/0 → 0/0 |
| `t0_-1_1` | 12/0 → 11/0 | 0/0 → 0/0 | 0/0 → 0/0 | 0/0 → 0/0 |
| `t0_-1_2` | 3/0 → 3/0 | 0/0 → 0/0 | 0/0 → 0/0 | 0/0 → 0/0 |
| `t0_-1_3` | 1/0 → 1/0 | 0/0 → 0/0 | 0/0 → 0/0 | 0/0 → 0/0 |
| `t0_0_1` | 8/0 → 7/0 | 0/0 → 0/0 | 0/0 → 0/0 | 0/0 → 0/0 |
| `t0_0_2` | 1/0 → 1/0 | 0/0 → 0/0 | 0/0 → 0/0 | 0/0 → 0/0 |
| `t0_0_3` | 6/0 → 5/0 | 0/0 → 0/0 | 0/0 → 0/0 | 0/0 → 0/0 |
| `t0_4_-4` | 2/0 → 2/0 | 0/0 → 0/0 | 0/0 → 0/0 | 0/0 → 0/0 |
| `t0_4_-3` | 3/0 → 3/0 | 0/0 → 0/0 | 0/0 → 0/0 | 2/2 → 2/2 |
| `t0_4_-2` | 4/0 → 3/0 | 0/0 → 0/0 | 0/0 → 0/0 | 6/6 → 6/6 |
| `t0_5_-4` | 0/0 → 0/0 | 1/0 → 1/0 | 0/0 → 0/0 | 0/0 → 0/0 |
| `t0_5_-3` | 3/0 → 3/0 | 0/0 → 0/0 | 0/0 → 0/0 | 0/0 → 0/0 |
| `t0_5_-2` | 2/0 → 2/0 | 0/0 → 0/0 | 0/0 → 0/0 | 0/0 → 0/0 |
| `t0_6_-4` | 4/0 → 4/0 | 0/0 → 0/0 | 5/26 → 5/26 | 0/0 → 0/0 |
| `t0_6_-3` | 2/0 → 2/0 | 2/0 → 0/0 | 0/0 → 0/0 | 0/0 → 0/0 |
| `t0_6_-2` | 0/0 → 0/0 | 4/14 → 2/7 | 0/0 → 0/0 | 0/0 → 0/0 |

## Controlled before/after rendering

Production WebGPU renderer, medium settings, draft materials, shadow batching, vegetation LOD and
post-processing; 1280×720, fixed noon lighting and fixed cameras. Forty warm-up frames and sixty measured
frames per view. Counts include all render passes. Both spot comparisons load the same successful
baseline tiles (11 Aura / 7 Häme); after tile overviews additionally include the repaired three tiles.
These are isolated-scene comparisons, **not a reproduction of the live playtest's FPS or streaming load**.
No claimed live-game FPS improvement is inferred from them.

| Spot | Oblique draws before → after | Oblique triangles before → after | Top draws before → after | Top triangles before → after |
|---|---:|---:|---:|---:|
| 01-teatterisilta | 148 → 126 | 652,757 → 507,560 | 167 → 134 | 739,130 → 519,295 |
| 02-theatre-boats | 157 → 131 | 631,827 → 472,559 | 175 → 140 | 755,670 → 555,075 |
| 03-riverbank | 172 → 147 | 794,948 → 551,814 | 199 → 152 | 800,681 → 519,776 |
| 04-myllysilta | 297 → 235 | 1,253,962 → 883,167 | 230 → 172 | 1,029,421 → 704,428 |
| 05-boat-slabs | 323 → 274 | 1,252,588 → 954,356 | 236 → 192 | 1,042,001 → 729,519 |
| 06-betanianpolku | 373 → 314 | 1,560,346 → 1,181,255 | 172 → 135 | 921,122 → 608,479 |
| 07-hameensilta | 240 → 203 | 1,292,051 → 805,910 | 164 → 130 | 925,308 → 487,838 |

Detailed frame/CPU/GPU samples: `before/render.json` (Aura), `before/render-hame.json`,
`after/render-aura.json`, `after/render-hame.json`. Browser usage was approximately 9.5 of the authorized
40 minutes. The task metadata incorrectly defaulted to five minutes; the final runs used a process-local
40-minute budget override while retaining the host scheduler and its append-only usage ledger.

## Reproduce and verify

Use the exact build and browser commands in
[the coarse README](../../pipeline/turku3d/coarse/README.md#real-data-generator-sanity-check).
Source `../sandbox.env` first, reserve host slots for builds/browser runs, and poll long commands at 30 s.
For the focused pure checks and unchanged legacy headroom tests:

```sh
source ../sandbox.env
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_bridges.py pipeline/tests/test_coarse_fences.py pipeline/tests/test_coarse_tunnels.py
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse.py -k osm_deck_height
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
```

Validation: 16 focused tests passed in 1.57 s; 4 legacy deck-height tests passed in 4.01 s.
The final `gate --fast --base main` was GREEN in 4.101 s (Python lint 0.638 s, policy 1.254 s,
JS lint 2.423 s; unchanged stages were cached or skipped and deep stages deferred as configured).

The new synthetic checks cover outline/parallel merging, actual underpass clearance, sampling below an
upper deck, branch-axis choice, ship holes, tile seam clipping, retaining/parapet/water placement and
tiny passage cuts. They supplement the real-data build/render inspection.

## Policy and remaining limits

The first separate commit adds the real-data sanity rule to `AGENTS.md` and explicitly allows the
required screenshots outside the fast gate. Immediate policy landing was attempted, but `codex-task
finish` refused because the task was still running. This was reported to the orchestrator; the DONE
turn leaves both committed slices for the runner to land, without merging main by hand.

Coarse riverbank stair-stepping, per-tile water levels, generic ship/building facades and vegetation
placeholders remain visible. These are existing draft terrain/appearance limitations outside this
bridge/barrier correction. The fast gate is the landing check; the integrator performs the later live
build and advisory gameplay checks.
