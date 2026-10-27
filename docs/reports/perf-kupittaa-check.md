# Kupittaa vegetation LOD and forest cap

The landed LOD still spent too much CPU on prototype/material draws and near-tree shadows.
On the cached drafts, the final fix reduces median CPU+GPU work from **5.23 to 4.82 ms**,
versus **5.15 ms before LOD**; p95 falls from **8.41 to 5.44 ms**. Drained throughput is
3.06 ms/frame versus 3.27 ms landed and 3.33 ms before LOD. The raised **768-tree cap**
produces a visibly denser forest at **5.92 ms median / 6.75 ms p95**, below the approximate
8 ms render-work target. On those same dense drafts, landed LOD costs 7.90 / 9.17 ms.

## Measurement

All figures are milliseconds unless labelled otherwise, the median of three independently
warmed 240-frame windows (120 warm frames each). Modes alternate forward/reverse in the same
headless Chrome 156.0.8078.48 process, on this M1 Max's Metal WebGPU backend, sandbox port 5250,
2560×1440, DPR 1, medium quality, 65° FOV and the previous probe's fixed autumn sun
(2026-10-25 12:00 Europe/Helsinki). Vsync is unlocked and each window ends with GPU completion
and timestamp readback drain. Every accepted window has 240 valid GPU samples and no page,
console or failed-asset errors. No browser spec or timing gate was added.

The location is the previous task's densest cached Kupittaa tile, `t0_5_2`; all nine tiles
`t0_4_1` through `t0_6_3` are loaded. Telemetry contains positions but no frame-time field,
so this is a reproduction of the identified hotspot, not a claim to have ranked every drive.
The inherited camera `(1375,14,625)` was **below the 17.17 m ground**. Visual inspection caught
this; accepted measurements use `(1375,19,625)`, looking at `(1420,18,450)`.

A temporary detached worktree at `382fb21c^^` (`e9738fb2`) provided the baseline. The paired
probe uses that exact shadow-batch implementation, the landed `382fb21c` LOD implementation,
and the working-tree implementation. Environment, post-processing and GPU timer sources are
unchanged across these revisions. It applies the real draft tint: the pre-fix tint is pinned
to `b893bb32`, while the fixed mode uses the revised constant PBR tint. This matters because
otherwise a benchmark could combine plain glTF parts while the game still uses node materials.

Calls and triangles include every pass. `workMs` is the timer's conservative paired CPU+GPU
sum, not wall-clock frame duration. Shadow cost is each repetition's shadow-on minus shadow-off
median work, then the median of those differences; its call/triangle deltas are exact counters.
It is an experiment-wide difference, not an isolated GPU shadow timestamp. CPU and GPU medians
need not sum exactly to the median paired work. Host load averages at the ends of the accepted
runs were about 67, 43 and 28; scheduling tails and small differences remain noisy.

| Drafts / step | CPU / GPU ms | Work p50 / p95 ms | Drained ms/frame | Draw calls | Triangles | Shadow Δ work ms / calls / triangles |
|---|---:|---:|---:|---:|---:|---:|
| Cached (1,391 trees) — Pre-LOD e9738fb2 | 2.29 / 2.77 | 5.15 / 7.75 | 3.33 | 117 | 1,992,359 | 0.66 / 16 / 688,574 |
| Cached (1,391 trees) — Landed 382fb21c | 2.58 / 2.63 | 5.23 / 8.41 | 3.27 | 131 | 503,694 | 1.12 / 24 / 67,925 |
| Cached (1,391 trees) — Fixed | 2.22 / 2.58 | 4.82 / 5.44 | 3.06 | 109 | 503,694 | 0.55 / 18 / 67,925 |
| Fresh cap 192 (1,421 trees) — Pre-LOD e9738fb2 | 2.13 / 2.61 | 4.80 / 5.46 | 3.00 | 113 | 1,685,627 | 0.62 / 16 / 585,710 |
| Fresh cap 192 (1,421 trees) — Landed 382fb21c | 2.61 / 2.69 | 5.31 / 6.21 | 2.92 | 119 | 425,404 | 1.27 / 18 / 26,783 |
| Fresh cap 192 (1,421 trees) — Fixed | 2.13 / 2.54 | 4.66 / 5.57 | 3.07 | 102 | 425,404 | 0.55 / 15 / 26,783 |
| Fresh cap 768 (2,391 trees) — Pre-LOD e9738fb2 | 2.90 / 3.06 | 5.98 / 6.84 | 3.42 | 121 | 2,903,869 | 1.46 / 16 / 1,102,560 |
| Fresh cap 768 (2,391 trees) — Landed 382fb21c | 3.89 / 4.00 | 7.90 / 9.17 | 4.17 | 145 | 556,758 | 2.30 / 24 / 80,993 |
| Fresh cap 768 (2,391 trees) — Fixed | 2.80 / 3.06 | 5.92 / 6.75 | 3.12 | 117 | 556,758 | 1.57 / 18 / 80,993 |

The cached GLBs are byte-identical across all three modes; they predate the forest producer's
cap (the hotspot already contains 263 mapped trees). For the fresh-density comparisons, only
vegetation nodes were replaced: both sets use identical original ground, buildings and other
meshes. The current producer read the same cached ground/OSM/clearance inputs for both caps.
No raw cache or shared draft was changed. Fresh cap 192 has 165 trees in `t0_5_2`; cap 768 has
460. Across the window this is 1,421 → 2,391 trees (+68%); bushes remain at their existing cap.
The old and fresh datasets must not be treated as identical density baselines.

The dense fixed version is about 25% cheaper than landed LOD on the same drafts. Its median
work is essentially tied with pre-LOD at that density; the 0.06 ms difference is not a meaningful
speedup. The initial cached comparison established the headroom before raising the cap. At
768, measured p95 is 6.75 ms; retaining the remaining margin is preferable to spending the
whole 8 ms on a stationary render, since driving also needs gameplay and streaming work.
This is not a whole-game driving-FPS measurement.

## Changes and rejected experiments

- Merge compatible opaque, untextured bark/crown parts with identical placements into one
  vertex-coloured instanced prototype. Near geometry and part colours remain intact; clustered
  geometry retains colours and far silhouettes use the original crown shape/colour. PBR,
  texture, transform or node-material differences keep independent draws. Disposal restores
  original sources and frees the generated resources.
- Keep the constant draft tree tint in ordinary PBR colour values instead of a node expression.
  Textured and other custom materials retain their existing path. A synthetic test checks the
  same luminance/saturation/cool-cast arithmetic and confirms real tinted parts combine.
- When a clustered crown already has at most 32 triangles, share its mid/far draw instead of
  generating a larger crossed silhouette. The separate experiment reduced calls 131 → 123,
  but the combined-parts change delivered the useful reduction to 109 on the cached window.
- Raise `TREE_CAP` from 192 to 768. Keep the 24-bush cap, biome densities, 5 m spacing and
  building/path/water exclusions. The full producer still writes trunk collision normally;
  these browser experiments render visual fixtures and do not simulate new collision.

A stationary-selection cache showed no clear CPU benefit and was removed. Early exploratory
captures missing copied shared textures are excluded from the accepted table. The old below-ground
camera and vsync-limited runs are also excluded. All raw exploratory and accepted records remain
local in `$TURKU3D_WORK/reports/kupittaa`; the accepted aggregate and asset hashes are in
[`perf-kupittaa-check.json`](perf-kupittaa-check.json). No imagery or raw city data is committed.
The sandbox-only browser budget temporarily matched the brief's explicit 30-minute allowance
(the runner omitted its numeric override); the normal gate configuration was restored afterward.
The original task ledger records about 16 browser minutes, including rejected experiments.

## Reproduce

The benchmark is manual and needs the pinned local draft snapshots. Keep all referenced
`shared/` textures alongside the tile directories. The command creates temporary probe files
and removes them on exit; use only the sandbox's Vite server and browser profile.

```sh
source ../sandbox.env
# Terminal 1
cd game && npm run dev -- --host 127.0.0.1
# Terminal 2, repository root; repeat with cap192 and cap768 instead of drafts
scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py browser -- \
  node qa/render/kupittaa.mjs --drafts "$TURKU3D_WORK/reports/kupittaa/drafts" --label kupittaa
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
.venv/bin/python -m pytest pipeline/tests/test_coarse.py -k forest -q
cd game && node_modules/.bin/vitest run tests/unit/vegetation-lod.test.ts
```

## Validation

`scripts/dev/gate --fast --base main`: GREEN, 4.536 s (environment sync, generated files,
Python lint, policy, game types, JavaScript lint and affected game units). The focused forest
unit selection passed 4 tests in 1.96 s; vegetation tests cover colour preservation, compatible
part merging, real draft tint compatibility, far colour, culling, budgets and disposal.
Deep and browser gate suites remain the integrator's advisory checks.
