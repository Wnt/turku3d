# Delivery and quick-suite measurements (2026-10-28)

All structural changes remain unlanded under the orchestrator hold while Jonni defines the target flow.
This report reads existing ledgers and task logs. It does not deploy or mutate shared work.

Reproduce: `python3 scripts/dev/iteration_metrics.py --days 3`.
Detailed data: [measurements](fix-iteration-pace-measurements.json), [current per-test inventory](fix-iteration-pace-quick-tests.json).

## Delivery

| Measurement | Observations | Median (s) | Maximum (s) |
|---|---:|---:|---:|
| merge_wall_seconds | 178 | 3.775 | 177.967 |
| lock_wait_seconds | 127 | 0.001 | 298.623 |
| fast_gate_seconds | 171 | 2.911 | 176.331 |
| source upload | 156 | 2.705 | 10.138 |
| Pages packaging/upload | 106 | 32.041 | 180.63 |
| code landing to Pages | 20 | 118.59 | 1006.273 |

Pages duration combines packaging and upload; older ledgers cannot separate them.
Publication timestamps exist for only the recent instrumented rows. Only commits in each published from..to batch count;
older already-published ancestors are excluded. No production after-change delivery result exists: this branch has not landed.

## Quick stage history

Counts come from completed gate command outputs in task event files modified within three days.
Raw events have no invocation timestamps; repeated known failures count again. Cached runs are excluded from runtime statistics.

| Stage | Executions | Cache hits | Failures | Median uncached (s) | Maximum uncached (s) |
|---|---:|---:|---:|---:|---:|
| advisory-chrome-tests | 65 | 37 | 1 | 9.357 | 14.188 |
| chrome-launch-policy | 75 | 2 | 0 | 1.854 | 47.243 |
| coarse-latency-unit | 31 | 12 | 0 | 1.467 | 2.425 |
| dev-server-tests | 99 | 58 | 0 | 3.347 | 28.782 |
| disk-retention-tests | 63 | 33 | 0 | 0.785 | 4.447 |
| draft-reference-score-unit | 13 | 7 | 0 | 1.173 | 2.621 |
| env-sync | 483 | 0 | 1 | 0.007 | 1419.817 |
| game-types | 283 | 110 | 5 | 1.024 | 40.179 |
| game-unit | 265 | 85 | 1 | 5.239 | 168.161 |
| generated | 498 | 371 | 1 | 0.5 | 18.939 |
| gmaps-compare-js-lint | 42 | 23 | 0 | 1.788 | 10.61 |
| gmaps-compare-unit | 40 | 22 | 0 | 0.893 | 5.895 |
| js-lint | 341 | 122 | 5 | 2.412 | 158.964 |
| package-js-tests | 106 | 56 | 0 | 0.413 | 5.718 |
| playtest-tools | 102 | 60 | 0 | 0.646 | 3.747 |
| policy | 516 | 0 | 41 | 0.796 | 548.65 |
| publish-tests | 86 | 49 | 0 | 1.61 | 7.682 |
| python-lint | 384 | 74 | 20 | 0.451 | 42.795 |
| qa-python-tests | 97 | 54 | 0 | 5.419 | 23.051 |
| remote-drafts-tests | 16 | 9 | 0 | 2.277 | 6.109 |
| shell | 94 | 54 | 0 | 0.454 | 3.389 |
| structures-physics | 108 | 59 | 1 | 2.517 | 544.849 |
| validator-types | 139 | 86 | 0 | 0.299 | 17.1 |
| voice-tests | 58 | 34 | 0 | 7.061 | 97.257 |

The current game-unit run reports 399 tests: 398 passed, zero failed and one skipped.
Its slowest test took 233.137 ms. Per-test durations/statuses are listed in the linked inventory.
Per-test historical failures are not retained; stage-level history must not be misrepresented as per-test history.

## Browser, deep and advisory history

| Spec/project | Observed runs | Failures | Cached | Latest test seconds |
|---|---:|---:|---:|---:|
| webgl:game/tests/e2e-hotswap/hotswap.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e-hotswap/telemetry.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e-playtest/aoi.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e-tile/area.spec.ts | 4 | 3 | 1 | 470.4 |
| webgl:game/tests/e2e-tile/bridge.spec.ts | 4 | 1 | 1 | 410.8 |
| webgl:game/tests/e2e-tile/deliveries.spec.ts | 4 | 1 | 1 | 3.1 |
| webgl:game/tests/e2e-tile/integration.spec.ts | 4 | 3 | 1 | 687.1 |
| webgl:game/tests/e2e-tile/minimap.spec.ts | 4 | 1 | 1 | 198.4 |
| webgl:game/tests/e2e-tile/missions.spec.ts | 4 | 1 | 1 | 0.7 |
| webgl:game/tests/e2e-tile/north-tile.spec.ts | 4 | 2 | 0 | 257.2 |
| webgl:game/tests/e2e-tile/parked.spec.ts | 4 | 2 | 1 | 1920.1 |
| webgl:game/tests/e2e-tile/shops.spec.ts | 4 | 1 | 1 | 222.7 |
| webgl:game/tests/e2e-tile/tile.spec.ts | 4 | 1 | 1 | 589.1 |
| webgl:game/tests/e2e/audio.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e/car-ghost.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e/character-look.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e/character.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e/coarse-drafts.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e/dev-tools.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e/edge-walls.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e/gamepad.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e/intro-dialog.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e/motion.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e/mp.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e/music.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e/parked-vehicle.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e/render-controls.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e/render-materials.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e/safe-reset.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e/save-menu.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e/scooter.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e/smoke.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e/start.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e/touch.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e/truck.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e/vehicle.spec.ts | 0 | 0 | 0 | unknown |
| webgl:game/tests/e2e/voice.spec.ts | 0 | 0 | 0 | unknown |
| webgpu:game/tests/e2e-hotswap/hotswap.spec.ts | 0 | 0 | 0 | unknown |
| webgpu:game/tests/e2e-hotswap/telemetry.spec.ts | 0 | 0 | 0 | unknown |
| webgpu:game/tests/e2e-playtest/aoi.spec.ts | 0 | 0 | 0 | unknown |
| webgpu:game/tests/e2e-tile/area.spec.ts | 4 | 3 | 1 | 188.4 |
| webgpu:game/tests/e2e-tile/bridge.spec.ts | 4 | 1 | 1 | 106.0 |
| webgpu:game/tests/e2e-tile/deliveries.spec.ts | 4 | 1 | 1 | 46.5 |
| webgpu:game/tests/e2e-tile/integration.spec.ts | 4 | 3 | 1 | 167.2 |
| webgpu:game/tests/e2e-tile/minimap.spec.ts | 4 | 1 | 1 | 38.3 |
| webgpu:game/tests/e2e-tile/missions.spec.ts | 4 | 1 | 1 | 230.8 |
| webgpu:game/tests/e2e-tile/north-tile.spec.ts | 7 | 2 | 0 | 160.8 |
| webgpu:game/tests/e2e-tile/parked.spec.ts | 5 | 2 | 0 | 1440.7 |
| webgpu:game/tests/e2e-tile/shops.spec.ts | 4 | 1 | 1 | 63.1 |
| webgpu:game/tests/e2e-tile/tile.spec.ts | 8 | 3 | 1 | 293.4 |
| webgpu:game/tests/e2e/audio.spec.ts | 0 | 0 | 0 | unknown |
| webgpu:game/tests/e2e/car-ghost.spec.ts | 0 | 0 | 0 | 6.4 |
| webgpu:game/tests/e2e/character-look.spec.ts | 0 | 0 | 0 | 3.0 |
| webgpu:game/tests/e2e/character.spec.ts | 0 | 0 | 0 | 5.5 |
| webgpu:game/tests/e2e/coarse-drafts.spec.ts | 0 | 0 | 0 | 28.4 |
| webgpu:game/tests/e2e/dev-tools.spec.ts | 0 | 0 | 0 | unknown |
| webgpu:game/tests/e2e/edge-walls.spec.ts | 0 | 0 | 0 | unknown |
| webgpu:game/tests/e2e/gamepad.spec.ts | 0 | 0 | 0 | 35.3 |
| webgpu:game/tests/e2e/intro-dialog.spec.ts | 0 | 0 | 0 | 14.0 |
| webgpu:game/tests/e2e/motion.spec.ts | 0 | 0 | 0 | 69.8 |
| webgpu:game/tests/e2e/mp.spec.ts | 0 | 0 | 0 | 182.4 |
| webgpu:game/tests/e2e/music.spec.ts | 0 | 0 | 0 | 11.9 |
| webgpu:game/tests/e2e/parked-vehicle.spec.ts | 0 | 0 | 0 | 8.3 |
| webgpu:game/tests/e2e/render-controls.spec.ts | 0 | 0 | 0 | unknown |
| webgpu:game/tests/e2e/render-materials.spec.ts | 0 | 0 | 0 | unknown |
| webgpu:game/tests/e2e/safe-reset.spec.ts | 0 | 0 | 0 | 66.5 |
| webgpu:game/tests/e2e/save-menu.spec.ts | 0 | 0 | 0 | 33.7 |
| webgpu:game/tests/e2e/scooter.spec.ts | 0 | 0 | 0 | 26.8 |
| webgpu:game/tests/e2e/smoke.spec.ts | 0 | 0 | 0 | 15.7 |
| webgpu:game/tests/e2e/start.spec.ts | 0 | 0 | 0 | 12.7 |
| webgpu:game/tests/e2e/touch.spec.ts | 0 | 0 | 0 | 7.7 |
| webgpu:game/tests/e2e/truck.spec.ts | 0 | 0 | 0 | unknown |
| webgpu:game/tests/e2e/vehicle.spec.ts | 0 | 0 | 0 | 6.6 |
| webgpu:game/tests/e2e/voice.spec.ts | 0 | 0 | 0 | 20.4 |

Chrome durations are latest test time including retries, not whole-suite wall time.
Zero observed executions mean unknown yield, not evidence of correctness. Interrupted runs are not counted as successes.

| Deep stage | Observations | Failures | Median seconds | Maximum seconds |
|---|---:|---:|---:|---:|
| dependency-imports | 34 | 0 | 49.949 | 148.522 |
| env-sync | 34 | 0 | 0.005 | 0.028 |
| gate-tests | 34 | 1 | 16.884 | 50.479 |
| integrator-tests | 34 | 2 | 68.556 | 242.531 |
| knip | 34 | 10 | 11.919 | 47.066 |
| pipeline-tests | 34 | 7 | 80.333 | 283.457 |
| textures-tests | 34 | 2 | 10.194 | 58.754 |
| tooling-tests | 34 | 5 | 76.984 | 205.094 |
| validator-tests | 34 | 0 | 0.0 | 184.574 |
| watch-tests | 34 | 0 | 0.0 | 15.293 |

| Advisory | Observations | Failures | Median seconds | Maximum seconds |
|---|---:|---:|---:|---:|
| car-report | 9 | 0 | 10.162 | 117.864 |
| chrome | 31 | 5 | 0.236 | 3062.648 |
| collision-checks | 28 | 5 | 8.191 | 87.811 |
| facade-ground-audit | 28 | 10 | 1.349 | 31.849 |

The prepared branch demotes exhaustive Chrome/deep runs to explicit investigations and keeps cheap changed-data audits.
This is a prepared decision, not a claim that the host is already using it. The agreed target flow may replace it.

## Slot and sandbox measurements

| Slot | Admissions | Median wait seconds | Maximum wait seconds |
|---|---:|---:|---:|
| browser | 504 | 0.039 | 2325.12 |
| build | 630 | 0.017 | 3453.233 |
| check | 4 | 0.251 | 0.524 |
| integration | 100 | 0.008 | 941.104 |
| integration-data | 80 | 0.006 | 3519.459 |
| land | 11 | 160.431 | 687.593 |

The host retains only current state.json, not a state-history archive. Admission waits are in waits.jsonl.
In this sandbox: public game bundle cold 4.044 s, hash-verified repeat 0.320 s; cloning built products 4.1 s;
roads-only refresh 1.681 s with identical records in two real tiles. The clone is a one-time setup cost.
These local measurements are not production landing-to-Pages measurements.

## Real tile repack cost

`make tile T=t0_0_0 AREA=playable-tiles` took **332.355 s**; the same command for
`t0_1_0` took **313.968 s**. Both exited zero, including format/seam validation.
Both repacked their visual bytes, so these are fresh-encoding costs, not cache-hit
measurements. [Exact hashes and timings](fix-iteration-pace-real-tiles.json).
They miss the five-minute goal despite zero slot queuing. They must not be folded
into the 1.681 s cached roads-refresh claim. A normal quick verification flow should
reuse published products and keep this fresh packaging out of its test phase.
