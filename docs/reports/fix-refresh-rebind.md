# Refresh rebind follow-up (2026-10-26)

## Cause and change

Three walls in the rebuilt inventory (`103277358N_27`, `103295900T_127`,
`103304670P_39`) are 2.7–4.0% longer than their approved source walls.
Their source UVs reach the image edge. The old horizontal reprojection shifted
the overflow range but could leave one end outside `[-0.02, 1.02]`, so a valid
small wall change stopped the whole refresh. The corrected reprojection scales
such a range into the photo while retaining its orientation. A wall over 10%
longer than its approved source is reported as `rejected` and left on its
library material. Approved photo hashes and UVs are checked before any fallback;
missing or corrupt source assets still stop migration.

`make refresh` now prints the cached/rebuilt status and elapsed time for each
step. The original 156-minute wall-clock run included 960.28 seconds of ground
rebuild and 84.90 seconds of props; the machine was asleep for roughly 1–2 hours
according to the orchestrator. No evidence points to a 150-minute compute step.

## Sandbox verification

Commands were run with the `fix-refresh-rebind` sandbox environment. The
sandbox started from cloned shared derived products; raw inputs were untouched.

```sh
PYTHONPATH="$PWD/textures:$PWD/pipeline" bash pipeline/env/run.sh python -m pytest -q \
  textures/tests/test_facade_rebind.py pipeline/tests/test_refresh.py
make refresh REFRESH_TARGET=facades
make refresh
make bridges-build
make tile-build T='t0_0_0 t0_0_1 t0_1_0 t0_1_1'
scripts/dev/gate --base main
```

- Focused tests: 14 passed in 1.69 seconds.
- Facade target: buildings cached in 1.70 seconds, facade migration rebuilt in
  4.90 seconds. Result: 82 kept, 236 reprojected, 40 new walls, 14 retired
  approvals, zero photo-bound rejections. The shared source has 316 approved
  maps, so the migrated result is 318 bound maps.

The 14 approved facade IDs that lost their old wall binding are:
`102725984D_114`, `103277350D_49`, `103277379B_123`,
`103277379B_124`, `103277379B_126`, `103277379B_128`,
`103304666K_44`, `103304666K_51`, `103304666K_75`,
`103304670P_38`, `103304672S_11`, `103304677X_73`,
`103440107M_288`, and `103440107M_305`. Their approved assets are archived
under `textures/assembly/retired/<inventory-hash>/`.
- Full refresh rerun: ground cached 0.006 seconds, buildings cached 1.604,
  props cached 0.054, facades cached 0.079, ground textures cached 0.140.
- Four L0 tile wire sizes after library material batching: 6,924,005;
  7,984,768; 7,995,421; 7,903,206 bytes.
  All are below 8,000,000 bytes. The assembler bound 318 facades and published
  100 collision tiles.
- WebGPU browser screenshots at the office spawn and Auransilta are in the
  sandbox's `work/reports/screenshots/` and `work/reports/bridges/` directories.
  Auransilta render and walk/drive tests passed.
- Initial rebind `scripts/dev/gate --base main`: green in 10.311 seconds.

The follow-up per-tile material identity change merges library-backed facade
classes into one `render_smooth` batch and roof classes into one
`roof_standing_seam_metal` batch. It preserves every source `material_key` and
index range in `extras.objects`; only the rendered material uses the canonical
library key. The synthetic fixture fell from eight building materials to two.
The rebuilt office tile fell from 38 to 34 materials. The 1440p office browser
test measured **487 draws on both WebGPU and WebGL2**, down from 502; both passed.
The batching count depends on library materials rather than the number of
buildings, so the pending additional building shells can reuse these batches.
The changed-path gate after batching was green in 35.298 seconds under host
contention.
The Auransilta view and facade color/ID QA passed on WebGPU. The WebGL Auransilta
view passed. WebGL facade QA reached color/ID capture, then twice exceeded the
repository's 120-second test limit while loading the aerial pose under host
load above 200. The same test passed alone in 2.9 minutes with a 240-second
command timeout; no repository timeout was changed.

## Approved export and prior draw failure

The shared export contains 316 approved maps. The earlier w1-09d run reported
26 additional approved maps and 344 final bindings, but its sandbox export is
not present in shared data or available worktrees. That export must be restored
or recreated before a main refresh can verify 344 bindings.

Before material batching, the office integration browser test measured 502
WebGPU draw calls at 1440p, against the required `<500`. The same command now
passes at 487 on both backends:

```sh
cd game && E2E_TILE_PORT="$((TURKU3D_PORT_BASE + 1))" \
  TILE_DIST="$TURKU3D_WORK/dist/tiles" TURKU3D_JOBS=1 \
  npx playwright test -c playwright.tile.config.ts \
  tests/e2e-tile/integration.spec.ts --project webgpu -g 'office integration'
```

The original assertion reported `Expected: < 500; Received: 502`. Neither
limit was weakened. The missing approved export is handled separately by the
orchestrator.
