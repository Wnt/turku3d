# lodblend handover: LOD bands, hand-overs and fades

Status on 2026-11-04. The full log is `~/turku3d/loops/lodblend/STATUS.md`, and the band map with its counts is
`~/turku3d/loops/lodblend/BANDS.md`. Captures, crops and frames stay local there and never go into git.

## The bands (BANDS.md in short)

Distances are 3D from the camera unless noted. The values are for the medium tier.

| Band | Where | What hands over |
| --- | --- | --- |
| Full tiles | `world/coarseTiles.ts` | A 3x3 window of 250 m tiles, kept to 325 m. A resident tile more than 450 m away (±40 m hysteresis, `TILE_DETAIL`) draws as its scenery cell. |
| Block shells | `world/buildingPlacements.ts` | HD buildings and the block LOD shell swap at 280 m (`BLOCK_LOD`, ±10 m band). |
| Props | `world/propLod.ts` | Street furniture hides at 125 m and shows again at 115 m. |
| Trees | `world/vegetationLod.ts` | Full mesh to 50 m, reduced mesh to 180 m, impostor to 650 m (`QUALITY.vegetation`). |
| Scenery quadtree | `world/sceneryView.ts`, `world/screenshotScenery.ts` | 250 m cells (L0) near, 1 km nodes (L2), then 4 km nodes (L4) and regional terrain to 15 km. Refinement is horizontal up to 250 m camera height; above that, the height past 250 m joins the distance (`refineDistance`). The L0 band never ends before 580 m (`L0_FLOOR`). |

- **Leaving tiles:** a full tile leaving the window draws until scenery fully covers its square, or until none can
  (`ScreenshotScenery.mayRelease`). There is no time cap. A tile 1.5 km away is let go uncovered.
- **Cells under tiles:** a cell under a resident tile stays loaded while the coarser node over it does not draw, so
  it can cover the square when the tile leaves.
- **Return cache:** detached full tiles are kept within 96 MiB of the typed arrays they own (`RETURN_CACHE_BYTES`).

## Fades and readiness

- **Timed dissolves** (`render/lodFade.ts`):
  - A far-band model draws through a dissolve twin of its material for 0.6 s (`LOD_FADE.seconds`). The twin uses one
    shared screen-door mask: a 4x4 Bayer threshold rotated every frame, which TRAA resolves into a blend.
  - The twin also carries a depth bias in three distance tiers.
  - Users: cut swaps (new nodes fade in over old ones), cells dissolving out over arriving full tiles, block shells,
    and full models fading in where no cell of their own draws (over a preview, a coarser node or nothing).
  - Fading meshes cast no shadow.
- **Readiness:**
  - Preparation compiles the twins (`precompileDissolve`). It keeps one detached keeper mesh per twin and attribute
    layout until the base material is disposed, because r186 frees a build once no render object uses it.
  - A dissolve starts only when its keepers exist (`LodFades.canStart`); otherwise the model swaps at once and logs
    `band.swap`.
  - Layer masks are not part of the readiness key, because scenery batches hide merged sources by clearing them.
- **Material copies:** `cloneNodeMaterial` copies keep the alpha test. r186's `NodeMaterial.clone()` drops it.
- **Terminal states:**
  - Every dissolve ends within its duration with the base materials back.
  - Only meshes inside a running dissolve draw a partial coverage (`drawnCoverage`).
  - Code that copies a mesh's material into a long-lived draw takes `stableMaterial`: vegetation pools, scenery
    batches, static batches and merged block shells.
- **Tree cross-fade** (`NatureMaterials.lodVariant`):
  - Each tree boundary fades per tree over a band just inside it: 24/16/10 % of 50/180/650 m. Each tree's band ends
    up to 8/6/3 % early, offset by a hash of its stable tint, so a tile never switches at once.
  - A tree in a band draws both levels, and their materials split its pixels with one per-frame threshold: no double
    coverage and no gaps.
  - The fade moves continuously with distance, so a slow climb or descent never flips a tree back and forth.
  - A full instance budget moves its boundary closer instead of dropping trees.
  - The impostor keeps its mip-0 alpha coverage (`IMPOSTOR_BOOST`: 1.0 through mip 4, 1.3 beyond).
- **Settle rule:** 0.25 s after the camera stops, the tree bands and the impostor edge-on fade narrow to a hard step
  over 0.5 s (`lodSharpness`). They widen again within 0.25 s of moving. A still frame keeps no dither.

## The stipple fix

airperf's A/B on the M1 Air showed a frozen screen-door stipple on some crowns and facades in settled frames.

- **Cause:** vegetation pools had copied a dissolving tile draw's twin together with its partial coverage.
- **Fix:** pooled tile draws sit out dissolves, coverage is drawn only inside a running dissolve, and copy sites take
  the stable material (8170bb88).
- **Result:** airperf confirmed the stipple gone on the Air.
- **Cost:** the tree fade cost no measurable beauty-pass time there.

## Switches (bench lab only, `qa/lab/params.ts`)

- `lodfade=S`: the timed dissolve length in seconds; 0 swaps at once. The default stays 0.6 s: it won the lodfade blind
  judge against 0-1.0 s (see lodfade.md). In the round-7 replay, dissolves ran in 46.5 % of frames at 0.6 s and would in 14.2 % at 0.15 s.
- `treefade=S`: scales the tree bands; 0 gives instant swaps.
  - The cost simulation at the riverside flight pose: +2.7 % vegetation triangles with the bands, +5.7 % on the
    ground, with the same draws.
  - Example: `?bench=static&cam=23460112.42,6704771.37,45,193,-13.6,60&time=19:30&treefade=0`.
- **Reports:** `tools/perf/tile_timeline.py CAPTURE` gives HD coverage of the view, holes and double coverage, and
  pops with the code's own swap reason.

## Open items, ranked

1. **One LOD chain (brief 5).** Full tiles, block shells, scenery cells and the 1 km nodes are separate products with
   their own looks and swap rules. A single per-building chain (HD, shell, cell geometry from one source) would remove
   the remaining look changes at 280 m and at the tile-to-cell switch. It would also let stream2's closer full-tile
   switch (about 250 m 3D) land without a visible step.
2. **1 km nodes seen from above.** Since the 3D refinement above 250 m, a camera higher than about 830 m sees only
   1 km nodes below it: survey shells without streets. Their geometry and ground need to hold up from above before
   the full-tile switch moves closer.
3. **Smaller items:**
   - Tile-GLB instanced street furniture, facade-module frames and pooled trees appear at the start of a full
     model's dissolve instead of fading.
   - A GPU bench comparison of `lodfade=0.6` against `0.15` (`tools/perf/bench_matrix.py 'bench=install' --variants 'lodfade=0.15'`).
