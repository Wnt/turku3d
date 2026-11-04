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
| W1 SSR (medium, reduced) | Medium and reduced have no planar pass. Each river fragment marches the flat mirror ray (12 + 4 steps, 400 m) over TRAA's history depth and reads its resolved colour, with the sky where nothing is hit. High and low keep the planar pass. | 163ff960, fixes 1e5cc203 and a0ebdee9, medium default on 2026-11-04 | `planar`; `ssr` and `sky` force a path on any reflecting tier |
| W8 rain | Rougher waves, drop rings within 35 m (behind a uniform branch, so dry water skips the arithmetic), and a duller, weaker mirror. | e9f99856 | none |
| W6 night | Light sprites (street lamps, floodlight heads) are mirrored about the sea-level plane at half size and half intensity, stretched 3x vertically. Depth-tested where the line of sight crosses the water plane. One more instanced draw. | b6682081 | none |
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
  - `quarter` (about -0.1 ms, blocky) and `lod` (no change) lost their A/Bs and are removed (510ec9fc).
  - `nodepth` saves about 0.1 ms, which is the bank fade's cost. `oldsun` shows no measurable difference.
  - Reflection off saves 8 ms of GPU p95 on the Air.

## SSR fallback: fixed, judged, medium default (2026-11-04)

1e5cc203 removed the bright streaky patch (sky at the visible display scale, coherent flat-ray march, 4-tap soft
reads). The re-shoot showed a second miss: from near water the mirror ray meets the far bank's hillside trees 100-250 m
away, past the 160 m reach (which faded from 88 m), so that water showed the sky where planar shows trees, and the
along-river view was washed out with ghostly trees. a0ebdee9 reaches 400 m (fading from 320 m), and tree-crown hits a
few metres behind the stored front fade out over 1-3 thicknesses (10 % of view depth) instead of dropping at 4 %.

**Blind A/B** (randomised pairs, refs vastavalo 1055994 and 330386; the Codex judge was out of quota until 2026-11-09,
so a fresh Claude subagent ran judge.py's A/B prompt): the fixed SSR is as close or closer to the references than
planar in all 10 pairs (9 wins, 1 tie: Mac night along the river), on the 560X (reduced), the M1 Max and the M1 Air
(medium). Before a0ebdee9, planar won the along-river day pair. Poses: across `cam=23459674.21,6704241.83,5.39,152,-6,60`,
along `cam=23459674.21,6704241.83,5.39,242,-6,60`; static bench, 14:00 and 22:00.

**Cost** (static bench, SSR against planar; GPU p95 over 240 in-page frames):

| Machine, tier | Across: FPS, GPU p95 | Along (heading 242): FPS, GPU p95 | Riverside preset: FPS, GPU p95 |
|---|---|---|---|
| 560X reduced, 910x569 | 60 / 60 (vsync), 18.6 / 19.5 ms | 60 / 56.9, 13.8 / 30.9 ms (planar pass 3.6 / 14.9) | earlier: 55.2 / 46.6 FPS |
| M1 Max medium | 89-91 / 72-79, 12.6-13.6 / 14.9-15.7 ms | 87-95 / 69-75, 11.3-12.5 / 17.3-17.6 ms | 61-66 / 57-65, 17.2-17.6 / 19.5-20.8 ms |
| M1 Air medium | 37.3 / 36.1, 25.8 / 29.2 ms | 40.2 / 37.0, 24.2 / 30.6-33.5 ms | 33.8 / 29.8, 28.8-29.4 / 39.3-40.8 ms |

The Mac ran at load ~45 (other agents), so its spread is wide; the Air was at thermal level 0 throughout. Planar's pass
cost on the Air medium is 3.6 ms across, 5.5-8 ms along and 11 ms on the riverside preset.

**Left for SSR:** rays that leave the screen fall back to the sky, which shows as soft bright patches at the left and
right image borders where planar shows a hull or bank, and lit windows mirror as stepped blocks at night (both paths).

**Edge fill, tried and lost (2026-11-05, branch `local/ssr-water`, 8353fd5e, parked):**
- What it did: a 0.2-3 % border fade instead of 12 %; a ray leaving through a side of last frame's image filled what
  the hit left with the colour where it left (only where that pixel stands 0.3-1.5 m above the water, at up to 0.7);
  a secant step on the bracket; four taps stretched 2.5x vertically; wave offsets 0.35x sideways, 0.6x vertically.
- Blind A/B against b4fce70d on the M1 Air (medium, both poses, day and night, refs vastavalo 1055994 / 330386):
  **the previous version won all 4 pairs** (2 clear, 2 slight). The fill turns the soft sky fade into hard bright
  vertical seams at the borders and cuts reflections off abruptly; the night windows stayed blocky in both.
- Cost on the Air: none along the river (35.2 against 35.0 FPS); about +1 ms GPU p95 across it before 8353fd5e
  limited the fill to side exits (not re-measured).
- Next idea: instead of copying the border column, fade the hit towards a blurred mean of the river's own reflection
  row (or the planar pass at 1/8 resolution only near the borders); for the night windows, smooth the wave normal
  used for the read offset (its low-resolution steps look like the cause), not the offset size.
- Tooling that held up for remote runs: static builds on osgallery-dev (built there with `VITE_TEST_HOSTS`), one
  perf lease per series from the Mac, the controller on the host side (osgallery-dev for the Air with the builds
  reverse-tunnelled to the Air's localhost, since a Chrome started from tmux has no macOS Local Network permission;
  Python on Windows), raw page CDP instead of Playwright's browser attach (it timed out on Chrome 156.0.8078.80),
  a 90 s load and 420 s bench timeout, touches only while the run log grows. Scripts: `~/turku3d/loops/ssrwater/`.

## Open items, ranked

1. **SSR screen-border fallback** (above), then **high:** measure `?water=ssr` on high against its planar pass (0.75
   scale); W2 block shells in the planar pass only matter for high and low now.
2. **Flow on flat reaches (the Aura) and foam via the generator (W4/W9).** Per-vertex flow from the OSM waterway
   centreline in `pipeline/turku3d/coarse/water_appearance.py` `visuals()`, shipped as a tilepatch migration of the
   water meshes plus generator parity, through one shared transform. The water meshes have vertices only on their
   boundary, so they need refining before per-vertex flow or foam strips mean anything. Foam strip meshes at banks
   and weirs would follow the same route. Shoreline foam on calm Aura water was left out on purpose.
3. **TRAA convergence on water (W7, second half).** GTA's faster convergence on water (current-frame weight 0.05 near,
   0.35 far) needs a fork of r186's `TRAANode`, with a water flag in the velocity MRT's spare channels. Correct river
   motion vectors are already in; this would only reduce the remaining ripple ghosting.
