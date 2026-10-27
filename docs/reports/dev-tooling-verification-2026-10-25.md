# Dev tooling verification logs (2026-10-25)

Moved from `scripts/dev/README.md` to keep it within the 800-line budget. These record the one-off verification runs of the rebase flow, the frozen-main integration and the frozen-main gate on 2026-10-25.

## Rebase verification (2026-10-25)

Rebased onto `7218b14` (including W1-06 `9523b80` and W0-C), preserving the native local pipeline,
controller tests and sandbox environment. Keyboard routes now hide host gamepads; virtual-controller
routes still inject their own inputs. No movement thresholds or timing limits were weakened.
The historical engine feasibility probes were subsequently retired by W0-B; their findings and archived
assets remain in the research documentation.

```bash
source ../sandbox.env
make -j 3 tooling-test foundation-test pipeline-test col-test game-test qa-test textures-test game-build game-dev-tools-test
make game-e2e
# Complete concurrent stacks, from this implementation branch:
scripts/dev/wt new verify-a --from wt/w0-tooling
scripts/dev/wt new verify-b --from wt/w0-tooling
# Terminal A:
source ~/turku3d/wt/verify-a/sandbox.env
cd ~/turku3d/wt/verify-a/repo
make game-e2e
# Terminal B, while A is running:
source ~/turku3d/wt/verify-b/sandbox.env
cd ~/turku3d/wt/verify-b/repo
make game-e2e
```

All requested area targets passed: tooling 12, foundation 21, pipeline 54, collision 38,
game 44, QA 23, textures 31 (2 optional dependency skips). Dist-server checks: 6 passed.
The area batch took 55.56 s; individual elapsed times were tooling 11.07 s, foundation 1.19 s,
pipeline 9.52 s, collision 52.52 s, game 2.41 s, QA 44.49 s, textures 4.58 s,
game build 1.36 s and dist-server tests 0.37 s. The final tooling rerun passed in 17.64 s.
A tooling run overlapping the browser stress checks exceeded the existing orientation/removal timing
limits; it was repeated successfully after the browser work finished, with those limits unchanged.

The final standalone game suite passed 16/16 in 154.22 s. Two complete stacks at `066d712`,
ports 5330–5339 and 5340–5349, passed 16/16 each concurrently in 220.72 s and 213.25 s.
Logs are retained in `~/turku3d/wt/w0-tooling/work/reports/rebase/`; temporary acceptance
sandboxes are removed. The project main branch was not modified. Main continued advancing during
verification; the tested integration base is explicitly `7218b14`.

Engine-probe build/Node checks passed in 38.52 s under concurrent load; its Chrome WebGPU
headless route passed in 27.37 s. Final `scripts/dev/here` completed in 0.37 s.


## Frozen-main integration verification (2026-10-25)

Rebased onto frozen main `ea0509d`, including W1-07 vehicle (`2fd13b0`) and W1-11 visual QA
(`8e8d2f9` through `ea0509d`). The QA loop and path-precedence implementation match main;
parallel judging, immutable evidence, calibration, regression and manifest provenance remain intact.
Sandbox integration retains the explicit `TILE_QA_PORT` / render `--port` overrides, defaults to
base +1 / +2 respectively, and uses the shared browser-profile helper. Vehicle screenshots now
use the sandbox reports directory. Active examples no longer reference the other workers' ports.

```bash
source ../sandbox.env
make tooling-test qa-test game-test game-build game-vehicle-test
make game-e2e
```

| Target | Result | Elapsed |
|---|---|---|
| `tooling-test` | 13 passed | 18.45 s |
| `qa-test` | 32 Python + 7 render tests passed | 28.09 s |
| `game-test` | 67 passed | 5.90 s |
| `game-build` | typecheck and production build passed | 21.04 s |
| `game-vehicle-test` | 22 game-WASM + 13 Node tests passed | 22.95 s |
| `game-e2e` | 18 passed, WebGPU and WebGL2; one run | 124.64 s |

An initial parallel batch exceeded the unchanged 2 ms streaming assertion and a 20 s tooling
removal timeout. Both affected targets passed when rerun separately; no checks or limits were
weakened. The build/vehicle timings include contention from that batch. Logs and machine-readable
results are under `~/turku3d/wt/w0-tooling/work/reports/rebase-ea0509d/`.
Main remains at `ea0509d`; this branch has not been merged into main.


## Frozen-main gate verification (2026-10-25)

Integrated W0-A tooling, W1-07 vehicles and W1-11 visual QA on main `58a85d0` (the final
addition after `3431e92` is the W0-D brief). All **19 gate stages passed in 139.799 s**.
The real-gate tooling suite passed 14 tests, including a trivial documentation landing,
a real oversized-binary rejection with main unchanged, and refusal when the gate is missing.
The project main branch was not modified.

Clean-tree samples used `scripts/dev/gate --base HEAD --json`, with each temporary edit
restored byte-for-byte afterward. Wall time includes process startup:

| Sample | File | Wall time |
|---|---|---:|
| Documentation | `README.md` | 0.688 s |
| Game core | `game/src/core/origin.ts` | 3.864 s |
| Pipeline fetch | `pipeline/turku3d/fetch/wfs.py` | 4.602 s |

The complete run passed 71 pipeline, 32 QA Python, 69 game unit, 7 QA render, 22 game
browser, 13 vehicle Node and 25 gate/tooling tests. Existing conditional skips remain:
one optional DeepBump test (not installed in this sandbox), and two multi-tile seam browser
tests (the offline gate builds one synthetic visual tile). No assertions or thresholds were relaxed.
JSON reports, the timing script and tooling output are retained under
`~/turku3d/wt/w0-gate/work/reports/w0-gate-integration/`.

