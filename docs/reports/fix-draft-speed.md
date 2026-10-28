# Draft request latency and quality

Kaskenkatu's next two draft crossings became visible and collision-ready before a 15 m/s
player reached them. The immediate pass now builds structural coverage; the full material,
survey, vegetation, kerb, fence and prop work upgrades that coverage in place.

## Request to edge-open

The manual replay uses production `CoarseTiles`, `CollisionStreamer`, GLTF/KTX loaders and a
real Rapier worker, with focus motion along the cached OSM Kaskenkatu alignment. It starts at
engine x 349, z 284 and moves at vx 8.34, vz 12.47 m/s. This measures streaming and actual
worker-loaded collision, rather than vehicle handling. Both target prepared-input caches were
removed before the final run; the raw file cache and persistent workers were warm. No network
fetch or facade enrichment ran in the diagnostic service.

| Crossing | Request → drawn + Rapier loaded | Arrival from replay start | Remaining margin |
| --- | ---: | ---: | ---: |
| t0_1_2 | 7.76 s | 17.32 s | 9.54 s |
| t0_2_2 | 10.38 s | 18.11 s | 7.71 s |

Requests started 0.014 s after replay start; the corresponding absolute opening times were
7.779 and 10.396 s. The relevant true edge was absent and the destination collision cell was
loaded at each opening. There were no page or physics errors. The incident has no precise
pre-fix edge-open timestamp: the edge stayed closed during the drive while the four builds
finished after 365–379 s.

Evidence: `$TURKU3D_WORK/reports/draft-speed/kaskenkatu-edge.json` and
`kaskenkatu-edge-open.png`. An earlier first-pass measurement exposed unnecessary decorative
input preparation; the final pass does not prepare those inputs.

## What consumed the minutes

The incident's existing manifests identify input preparation as the largest cost. For
`t0_2_-2`, total 364.515 s contained WFS 156.756 s, poles 46.495 s and visuals 41.079 s.
`t0_-2_-1` spent 247.485 s in WFS; `t0_-2_-2` spent 212.087 s in roads. WFS refines roof
planes against classified lidar; roads prepare bridge survey evidence. Four full producers
competed on the busy host, each importing/decoding/preparing overlapping sources.

The unchanged producer captured at task start was profiled on a near-centre and outer tile:

| Tile | Original full, cold prepared inputs | Final first pass | Subsequent full upgrade |
| --- | ---: | ---: | ---: |
| t0_2_-2 | 58.519 s | 4.919 s | 21.377 s |
| t0_5_-3 | 63.293 s | 1.277 s | 7.869 s |
| t0_-1_1 | — | 5.573 s | 14.920 s |
| t0_-2_2 | — | 11.613 s | 13.527 s |

These are generation times, excluding client loading. The final full upgrades reuse first-pass
base WFS and existing decoded survey/texture caches. The first cold survey upgrades in this
sandbox took 73.201 / 54.310 s for the two bridge tiles; that work now stays behind the first
pass. These runs shared the active integration/content host, with observed load averages around
37–89. They are operational measurements, not a fixed-load speed ratio. The original cold
near-centre profile spent 18.691 s in roof refinement (including 10.376 s obtaining class-6
points), 7.615 s in material extraction and 6.742 s in boundary splitting. Outer material
extraction alone took 35.428 s. First-use facade kit initialization took 5.572 s.

For separated codec/packager stages, the saved original producer was also instrumented and
replayed through the admitted diagnostic service. That comparison had warm decoded source
caches and one native thread; it must not be confused with the cold profile above:

| Stage, seconds | Original near / outer | Final first near / outer | Final full near / outer |
| --- | ---: | ---: | ---: |
| Terrain geometry | 0.102 / 0.100 | 0.227 / 0.051 | 0.212 / 0.121 |
| Bridges | 0 / 0 | 0 / 0 | 0 / 0 |
| WFS + roofs | 4.197 / 1.572 | 0.814 / 0.261 | 3.001 / 0.323 |
| Building shells | 0.024 / 0.043 | 0.180 / 0.052 | 0.061 / 0.059 |
| Surface boundary cuts | 2.525 / 3.596 | 0 / 0.001 | 5.783 / 4.160 |
| Ground map extraction | 4.330 / 0.800 | 0 / 0 | 1.195 / 0.002 |
| Facade/prop atlases | 0.012 / 0.006 | 0 / 0 | 0.028 / 0.008 |
| Texture codecs/cache | 1.280 / 0.234 | 0 / 0 | 0.015 / 0.009 |
| Compression + decoded verification | 0.246 / 0.294 | 0.097 / 0.041 | 0.440 / 0.386 |
| Collision | 0.149 / 0.188 | 0.311 / 0.174 | 0.182 / 0.266 |

First-pass bridge production on the two bridge tiles took 2.456 and 6.365 s; collision took
0.475 and 2.477 s. `seconds` also records binding, batching, snapshots and the aggregate Python
packager time, including kit initialization. Host contention varies between stages and runs.
Full stages are not claimed to improve uniformly; the deadline comes from separating critical
coverage from expensive work and retaining the caches.

## Implementation

- One first-pass lane and at most one full upgrade replace four competing full builds. Actual
  velocity-ray footprint entry times rank the next crossings before lateral/behind tiles.
  The client submits requests in that order too, so the first idle worker receives a forward
  cell. Urgent crossings precede upgrades; lateral work cannot starve full quality forever.
- First passes prepare ground, base WFS buildings, roads/bridges and tunnels, with flat
  materials and all 16 collision cells. Missing raw inputs return pending without starting
  full preparation. Decoration, orthophoto sampling and classified surveys run in the full pass.
- BLAS/OpenMP, Arrow, LAZ and image pools are bounded. Upgrade workers use `nice -n 10`;
  first-pass ETAs exclude upgrade service times and use their dedicated worker. Process
  priority was checked separately with the current worker launcher.
- Input cache identities track each dataset's producers. A surface edit retains prepared roofs,
  bridges and ground. Material extraction identities exclude unrelated surface geometry.
  Full WFS reuses base shells. Roof refinement crops cached class-6 points to the buildings
  before constructing its search tree; a synthetic equivalence check preserves planes/evidence.
- Existing content-addressed encoded textures remain shared. Draft linear maps use lossless
  Zstd level 6 instead of 18 with the same UASTC quality and dimensions. Identical encoded maps
  are reused; no texture resolution or full geometry detail was reduced.
- Payload URLs contain hashes. First-pass readers therefore retain valid bytes while a full
  manifest is published. The client holds the old model/collision until the upgrade is ready
  and updates its road graph too. Producer fixes bypass an obsolete failed-build backoff.

The fast gate exposed a pre-existing supervisor race: its restart promise ended before the
replacement service was ready. The promise now covers readiness and repeats when another
relevant landing arrives after spawn. The existing restart test passes. Google comparison also
needed a consent wait correction: hidden retained DOM buttons no longer cause a timeout after
successful rejection; persistent rejection cookies are still required.

## Real-data sanity and quality

Four tiles were built in both passes. Forty-eight final game-renderer images cover a top-down
and oblique view of every tile and every source bridge instance in `t0_-1_1` and `t0_-2_2`.
Prepared OSM groups and produced deck counts match: five and three, including Teatterisilta,
Myllysilta, Martinsilta and mapped small landings. Clipped neighbouring portions are not extra
crossings. No new misplaced deck or water-crossing barrier was observed. Existing serrated
river banks remain unchanged.

All four final full GLB SHA-256 values equal the earlier full-quality iteration. Four matched
pre-fix/full overview pairs have mean absolute RGB difference below 0.001 on a 0–255 scale.
Six asphalt/grass/slab normal and ORM maps decode to identical pixels at identical dimensions
across the compression change. The first pass is intentionally plain; the full pass restores
the detailed maps, facade kit, surveyed bridge profiles, kerbs, fences, trees and props.

Local Google comparisons were captured for Myllysilta and the near-centre river frontage.
The tool calibration is **UNVERIFIED**; no pose-aligned height/colour score is claimed. The
temporary 3D Tiles mount did not give a useful game frame, so local `gmaps-draft.png` pairs
use the direct draft GLTF/game-renderer fixture at the returned effective camera. Original
captures and metadata are retained. The pairs show the source bridge and frontage in the
expected places, alongside the existing simplified vegetation/river bank. Reference images
remain under `$TURKU3D_WORK/qa/gmaps-compare/`, never in generation inputs or the repository.

Other evidence under `reports/draft-speed/`: `timings.json`, `baseline-stages.json`, both
`.prof` files, `inventory.json`, `first/`, `full/`, contact sheets, `texel-check/results.json`,
`image-check.json` and the immutable full hashes. Source/raw inputs were not modified.

## Reproduce

```sh
source ../sandbox.env
# Terminal 1: cached sources only; no raw fetch or facade enrichment. Use a fresh root for a cold run.
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python tools/perf/draft-speed-service.py
# Terminal 2:
npm --prefix game run dev -- --host 127.0.0.1 --port "$TURKU3D_PORT_BASE" --strictPort
bash pipeline/env/run.sh python tools/perf/draft-speed.py --service "$((TURKU3D_PORT_BASE+5))"
python3 scripts/dev/host_slots.py browser -- node qa/render/draft-edge.mjs
# Direct sequential generation is also available (no service):
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python tools/perf/draft-speed.py
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_latency.py pipeline/tests/test_coarse_surfaces.py
(cd game && ./node_modules/.bin/vitest run tests/unit/coarse-tiles.test.ts)
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
```

The retained `views-standard.json` drives `qa/render/texture-sanity.mjs after` with the local
`reports/texture-sanity/after` symlink selecting `first/` or `full/`. Run captures serially;
Vite reloads when another fixture changes its temporary HTML/JS or when sources rebase.

The unchanged deep roof test `test_passage_keeps_upper_terrace_and_never_resurrects_lower_roof`
still exposes an older empty-roof fallback defect; it was reported to the orchestrator. It is
outside this latency change and was not weakened. Fast-gate validation is recorded in the task result.
