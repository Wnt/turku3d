# W1-08: Photoreal rendering v1 at 1440p60 (Phase 1 task 1.15)

Read `docs/briefs/COMMON.md` and `AGENTS.md`. You own `game/src/render/`.

## Goal

The block should look like a photo of Turku on a clear autumn afternoon, at **60 fps at 1440p in Chrome on the M1 Max**
on WebGPU, and at least 45 fps on WebGL2.

## Deliverables

1. **Lighting**:
   - Physically based sun and sky (takram atmosphere or an equivalent that runs under `three/webgpu`; smoke-test it
     first, see `docs/research/engine.md`), with the sun position from date, time and Turku lat/lon.
   - IBL from the sky.
   - Cascaded shadow maps tuned for street scale: crisp under 50 m, stable, no shimmering.
2. **Post-processing**:
   - GTAO kept light, because the facade albedo already carries some AO.
   - TRAA/TAA, bloom, AgX/ACES tonemapping and exposure.
   - Subtle colour grading.
   - Each effect has a quality tier: low, medium, high.
3. **Materials**: PBR material setup driven by `material_key`, using the texture lookup in COMMON §4.
   - Wet and dry variants are not needed now.
   - Water: a reflective river shader with planar or SSR reflection and normal-map waves.
   - Asphalt, granite curb, cobblestone and grass shaders where the library textures need more.
4. **Performance instrumentation**:
   - GPU timestamp queries on WebGPU, frame-time percentiles, and draw-call and triangle counts in the HUD and in
     `window.__perf`.
   - A Playwright perf test at 2560×1440 with a fixed camera path: p50/p95 GPU and CPU time per backend against the
     budget (16.6 ms total), recorded per commit.
   - Fix the HiDPI screen-space-error inconsistency noted in Phase 0. Pick one rule and document it.
5. **Time of day**: a `?time=ISO` parameter (the QA contract already uses it) and a debug slider.

## Acceptance

- WebGPU p95 frame ≤ 16.6 ms at 1440p on the real AOI tiles (or the synthetic dev tiles until the real ones land; say
  which). WebGL2 p95 ≤ 22 ms.
- Before/after screenshots from the QA views attached in the result.
- No visual regression in the QA contract tests.
