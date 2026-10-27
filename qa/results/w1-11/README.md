# W1-11 measured evidence (2026-10-25)

Status: **BLOCKED on the assembled building/structure AOI**, not on tooling tests.
The available package is the Phase 0 ortho-draped terrain. The game renders actual package
geometry, not the QA contract stub. No building asset IDs exist in that package.

- 20 reference views from all five requested source families; 20 colour + 20 ID captures,
  WebGPU in Chrome, exact pose echo and viewport checks passed. All ID pixels were also
  checked against their tables (0.55 s for the saved 20 pairs).
- Baseline `w1-20-aligned`: mean 0.55/10, 58 issues, 26 with visible asset candidates.
- Commit `2fc2a3f`, run `w1-20-2fc2a3f`: mean 0.60/10, 59 issues, 28 with visible asset
  candidates. `../../issues.jsonl` contains this run's tickets. Missing assets explicitly
  remain unmapped; the background ground is not misidentified as a missing building.
- The latter run rejudges the same saved renders with immutable reference snapshots; it
  records a clean commit. The baseline records its dirty worktree honestly. Mean delta
  +0.05 passes the 0.5-point regression margin. **This is judge variation, not evidence of
  an asset improvement.** A fix-and-rerun cycle is still needed after the assembled AOI lands.
- Three independent calls on the same real drone pair: [3, 3, 3], sample variance 0,
  standard deviation 0. This is one low-score pair, not a global reliability estimate.
- 43 successful judge calls / 43 attempts; 856,125 input tokens and 27,797 output tokens.
  The observed account-wide counter ranged 7% to 14% during calls. Parallel agents were
  active, and these integer counters cannot attribute the seven percentage points solely
  to this task. No paid service, new key or image-generation call was used.
- Drone heading: grid offset -1.00208 degrees from two manually identified church spires;
  fit residual 0.03876 degrees is not absolute accuracy. GPS/altitude uncertainty remains.
- Photo-to-lidar intensity/SIFT attempts on sv_01, sv_02, la_02 yielded 0, 1, 2 depth-backed
  matches. None meets the minimum 16; no pose correction was accepted. See the JSON evidence.
- Gate: QA pytest 30 passed, browser contract 6 passed, game vitest 30 passed. Combined
  `make -j2 qa-test game-test` wall time 11.96 s. Game production typecheck/build also passed
  (Vite build 1.03 s). A repeated fresh setup installs missing QA dependencies via the stamp.

The private, self-contained 12 MB report (reference photos, game images, boxes, scores,
asset mappings, calibration and trend) is:

`/Users/wnt/turku3d/wt/w1-11-visualqa/work/qa/w1-11-report.html`

It is deliberately outside git: reference photos must not be shipped as game assets.
The run directories, rendered screenshots, raw judge outputs, token counts and thread IDs
remain under `$TURKU3D_WORK/qa`. The committed JSON files retain reviewable measurements.

## Reproduce the checks

From this worktree root:

```sh
source ../sandbox.env
TURKU3D_PORT_BASE=5251 make -j2 qa-test game-test
make game-build
qa/.venv/bin/python qa/regress.py --history qa/results/w1-11/history.jsonl \
  --baseline w1-20-aligned --current w1-20-2fc2a3f --margin 0.5
qa/.venv/bin/python qa/acceptance.py --run "$TURKU3D_WORK/qa/runs/w1-20-2fc2a3f" \
  --calibration "$TURKU3D_WORK/qa/calibration/calibration.json"
```

Regression exits 0 with `mean_delta: 0.05`. Acceptance exits **1** with:

```json
{
  "ok": false,
  "views": 20,
  "blockers": [
    "current package exposes no building asset IDs; assembled building layer is required"
  ]
}
```

## Concrete remaining work

1. W1-04: supply the assembled AOI including W1-01 ground, W1-02 structures and W1-03
   buildings, preserving building/facade/material/tile IDs. Copy that immutable package
   into the sandbox and run `make tile-qa` again with a fresh `QA_RUN`.
2. Select a mapped permanent-asset issue, fix the owning asset, rerender and rejudge the
   same views. The current low-score ground-only package cannot establish facade fidelity.
3. Improve pose registration with surveyed/manual 2D–3D landmarks or validated feature
   registration; current airborne intensity render features do not match the photos
   sufficiently. Boat/Mapillary height and roll/pitch remain approximate.
4. Rebase on Wave 0/main when the orchestrator lands tooling. This branch does not merge
   to main and uses the area test targets because `scripts/dev/gate` is absent here.
