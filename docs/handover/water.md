# water handover: GTA-style river (techstudy W1-W8)

Status on 2026-11-04. The full log is `~/turku3d/loops/water/STATUS.md`. The techniques come from the GTA V water study,
`~/turku3d/loops/techstudy/REPORT.md` §8. Winperf's screenshots stay local in `~/turku3d/loops/winperf/shots/`.

## What is in integration

Everything is client code: no tile rebuild, F5 is enough. Each step has a `?water=` token for its A/B
(`game/src/render/waterSwitches.ts`). The tokens work in play, the bench lab and replays.

| Step | What it does | Integration | Token (restores the old path) |
|---|---|---|---|
| W5 sun | Tight sun glints (energy-normalised Phong, exponent 1800 near, relaxing to 220 by 450 m). They fade where the planar image shows a building rather than sky along the reflected ray. Wrap diffuse gives wave facets facing the sun a lighter body colour. | 2a3dcc7d | `oldsun` |
| W3 bank fade | With TRAA on, the river writes no depth and reads the previous frame's opaque depth from TRAA's history, with no extra pass. The body colour thickens over 0.45 m of water, so quay walls and banks fade into murky water. | ec5e5220 | `nodepth` |
| W7 motion | The river writes its own TRAA motion vectors. Before, it took the sky box's motion, and ripples smeared while moving. Medium and reduced only: on high, TRAA motion comes from the GTAO prepass, which excludes the river. | ec5e5220 | `nomotion` |
| W1 reduced SSR | Reduced has no planar pass. Each river fragment marches the flat mirror ray (12 + 4 steps) over TRAA's history depth and reads its resolved colour, with the sky where nothing is hit. | 163ff960, fix 1e5cc203 | `planar`; `ssr` and `sky` force a path on any reflecting tier |
| W8 rain | Rougher waves, drop rings within 35 m (behind a uniform branch, so dry water skips the arithmetic), and a duller, weaker mirror. | e9f99856 | none |
| W6 night | Light sprites (street lamps, floodlight heads) are mirrored about the sea-level plane at half size and half intensity, stretched 3x vertically. Depth-tested where the line of sight crosses the water plane. One more instanced draw. | b6682081 | none |
| W2 A/Bs | `quarter`: planar at 0.25 with a mip blur. `lod`: no straight-up ground in the planar pass. Neither is on by default. | f3bf5abd | opt-in `quarter`, `lod` |
| W4 sloped reaches | Streams and weirs (`water_slope`, 144 served tiles) flow downhill through a two-phase flow, with the slope taken from each triangle. Gradients of 8-25 % foam white. | bc74c42c | none |

Code: `render/riverSurface.ts` (surface graph), `render/water.ts` (`RiverMaterial`, `riverReflection()` picks
planar, ssr or sky), `render/riverReflection.ts` (the march), `render/waterHistory.ts` (TRAA history, published by
`post.ts`), `render/lights/sprites.ts` (W6). Tests: `tests/unit/river-shader.test.ts` builds the river's WGSL for every
variant on the CPU. Before merging, also validate shaders with naga on osgallery-dev (`~/.cargo/bin/naga file.wgsl`):
the boot check only compiles medium's default path.

## Measured

- **Windows, Radeon Pro 560X, reduced (winperf):** no WGSL, validation or page errors.
  - Along the river: SSR **55.2 FPS against 46.6 with `planar`**; beauty p95 11.8 against 18.2 ms; 318 against 501
    draws at night. The planar reflection pass (5.1 ms) is gone, and the march costs under 0.1 ms.
  - Looking across the river (cam `23459674.21,6704241.83,5.39,152,-6,60`): all variants are vsync-capped.
- **M1 Air, medium (airperf, `docs/handover/airperf.md`):**
  - `quarter` saves about 0.1 ms on average (-0.45 ms per reflection frame) and looks blocky. **Drop it:** remove the
    token and its path in `water.ts`.
  - `lod` changes nothing: drop it too.
  - `nodepth` saves about 0.1 ms, which is the bank fade's cost. `oldsun` shows no measurable difference.
  - Reflection off saves 8 ms of GPU p95 on the Air.

## SSR fallback fix: re-shoot pending

On the across-the-river pose, winperf's first shots showed SSR mirroring the far quay sharply, but a bright, streaky
patch where it fell back to the sky. Fixed in 1e5cc203:
- The PMREM sky is baked at brightness 1, while the visible sky (and so the planar mirror) is drawn at the 0.45 day
  display scale. Every sky-only reflection now uses the visible scale. This also darkens the over-bright sky
  reflection on mobile, sloped reaches and `?water=sky`.
- The march follows the flat mirror ray, so hits are spatially coherent; the waves only shift where the hit is read.
- Hits fade near the screen border, towards the end of the march and near the thickness limit. They are read as 4 taps
  1.5-4 texels apart, for planar-like softness.

**Not yet seen on a GPU.** Re-shoot `water-a-{ssr,planar,sky}-day` and the night pair on the 560X.

## Open items, ranked

1. **W2 with block shells.** The planar reflection should draw block LoD2 shells instead of HD buildings and facade
   detail. This needs per-camera LOD visibility in `world/buildingPlacements.ts`: shells are hidden while HD shows, so
   it needs a layer switch rather than `visible`. Given the Air's 8 ms reflection p95, first try `?water=ssr` on medium
   on the Air once the fallback re-shoot is good. SSR may beat any planar optimisation.
2. **Flow on flat reaches (the Aura) and foam via the generator (W4/W9).** Per-vertex flow from the OSM waterway
   centreline in `pipeline/turku3d/coarse/water_appearance.py` `visuals()`, shipped as a tilepatch migration of the
   water meshes plus generator parity, through one shared transform. The water meshes have vertices only on their
   boundary, so they need refining before per-vertex flow or foam strips mean anything. Foam strip meshes at banks
   and weirs would follow the same route. Shoreline foam on calm Aura water was left out on purpose.
3. **TRAA convergence on water (W7, second half).** GTA's faster convergence on water (current-frame weight 0.05 near,
   0.35 far) needs a fork of r186's `TRAANode`, with a water flag in the velocity MRT's spare channels. Correct river
   motion vectors are already in; this would only reduce the remaining ripple ghosting.
