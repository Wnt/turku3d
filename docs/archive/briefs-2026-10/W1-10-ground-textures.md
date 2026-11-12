# W1-10: Ground textures: ortho drape plus material library (Phase 1 task 1.9)

Read `docs/briefs/COMMON.md` and `AGENTS.md`. You own ground material and texture generation, together with
`game/src/render` materials (coordinate with W1-08 through the material API, not by editing the same functions in
parallel).

## Goal

At street level the ground looks like real Turku streets: asphalt with markings, granite curbs, cobblestones,
sidewalk slabs and grass. It must not look like a blurry 5 cm aerial photo. Up close the detail comes from tiling
library materials. From further away the ortho gives the real layout (markings, wear, colour variation).

## Deliverables

1. **Ortho products per visual tile**:
   - The 2022 true ortho (primary, no lean) and the 2025 ortho for recency, colour-matched.
   - Cars, people and shadows reduced, for example by a median or minimum across years and a de-shadowing heuristic.
   - Encoded as KTX2 at ≤ 4 cm/px with mips.
2. **Material splat**: per-triangle material IDs from the ground layer plus ortho segmentation give splat or blend
   weights per visual tile.
3. **Library ground materials**: asphalt (fresh and worn), granite curb stone, cobblestone, concrete slabs, grass,
   gravel, quay stone. Generate them with Codex image_gen (through the `textures/` runner) or take them from CC0
   sources (ambientCG, Poly Haven), recording licences. Process them into tileable PBR KTX2.
4. **Road markings**: crosswalks, lane lines and stop lines as crisp decals from the kantakartta and OSM, with the ortho
   used only for checking.
5. **Shader**: in the game, a ground material that blends the ortho at distance with tiled detail up close by
   splat weights, with no visible tiling (stochastic or hex tiling).

## Acceptance

- At 1.7 m eye height the ground is sharp and believable. Show screenshots from 3 QA views, before and after.
- There are no colour seams at tile edges.
- The KTX2 GPU memory budget per L0 tile is reported and within ARCHITECTURE D4.
- Fast unit tests for splat weight generation.
