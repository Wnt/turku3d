## Result: DONE

All public project browser launch sites use the shared Chrome for Testing resolver. The required fast gate is green. Browser suites were exercised as advisory verification; their failures and the incomplete tile run are recorded below.

## What changed

- `tools/dev/chrome-args.mjs` provides `chromeExecutable()`, defaulting to the shared macOS ARM64 CfT 156.0.8078.73 installation under `~/turku3d/browsers/`. The pin matches Google's stable CfT manifest checked on 2026-10-28. `TURKU3D_CHROME` accepts an absolute executable override. Missing/nonexecutable files fail; desktop Chrome and symlinks to it are rejected.
- Migrated every listed launcher and later additions: Playwright configs, embedded Node previews in Python, game/QA probes, compare/video/music tooling, native CDP verification and the newly landed local shadow capture. Python previews import the same JavaScript resolver rather than duplicating executable selection. Integrator/advisory launches inherit the protected configs. Private Google tooling and remote Air sessions are unchanged.
- Retained merged `MacAppCodeSignClone` protection and Playwright defaults. Every shared argument list now forces `--use-mock-keychain` and `--password-store=basic`, including native CDP. The first native probe exposed the missing mock-keychain protection; the corrected CDP/music run completed unattended with both flags verified through CDP. No login-keychain access was granted or performed by the agent.
- The uncached fast policy rejects desktop paths/channels and launch options without the resolver/shared arguments, checks native launches, scans every Playwright config (including inherited configs), and requires both keychain defaults. Synthetic tests cover these rules and resolver symlinks.
- Removed QA's old `--channel` bypass and fingerprinted the resolver/override in comparison keys. Updated cleanup fixtures and developer/QA documentation. Exact installation commands are in `scripts/dev/README.md`; the launch contract is in `scripts/dev/browser.md`.

## How to verify

From this sandbox with its exported environment; poll long commands every 30 seconds or wrap them with `scripts/dev/codex-task wait-cmd --`:

```sh
node tools/dev/check-chrome-launches.mjs
node --test tools/dev/chrome-args.test.mjs
.venv/bin/python scripts/dev/tests/run.py test_stop.py
(cd game && python3 ../scripts/dev/host_slots.py build -- ./node_modules/.bin/vite build)
python3 scripts/dev/host_slots.py browser -- node tools/dev/verify-chrome-clone.mjs
(cd game && E2E_PORT=5212 python3 ../scripts/dev/host_slots.py browser -- ./node_modules/.bin/playwright test -c playwright.render.config.ts)
(cd game && E2E_TILE_DEV=1 TILE_DIST="$(realpath "$TURKU3D_DATA/live/tiles")" python3 ../scripts/dev/host_slots.py browser -- ./node_modules/.bin/playwright test -c playwright.tile.config.ts)
scripts/dev/gate --fast --base main
```

The tile invocation uses current source through its supported dev-server mode and a resolved, read-only live tile tree. The render invocation uses the production game built from this checkout. No source tests or thresholds were weakened.

Verification artifacts remain under `$TURKU3D_WORK/reports/`:

| Run | Result | Process evidence |
| --- | --- | --- |
| CDP + music-lab | Passed, 21.85 s; `navigator.gpu` and adapter both true; Chrome 156.0.8078.73; no new clones | 19 samples, 2 CfT processes |
| Full render suite | 4 passed, 6 failed, 248.05 s | 133 samples, 10 CfT processes |
| Tile suite, partial | 10 passed, 3 failed, 23 unrun, 1058.13 s; gracefully interrupted the parked-car retry | 609 samples, 14 CfT processes |

All sampled browser processes had `--use-mock-keychain` and `--password-store=basic`. Repeated `pgrep -af 'Applications/Google Chrome.app'` checks found no browser from this sandbox using desktop Chrome. Exact commands, sampled process arguments, timings and desktop matches are retained in `browser-separation/{cdp,render,tile}.json`; suite output is in the adjacent `.log` files. CDP/clone details are in `chrome-clone/verification.json`.

The real-tile draw/facade/Auransilta, traversal, delivery, office facade/ground, minimap and north-streaming checks passed. Both 1440p performance specs and functional diagnostic specs passed. WebGPU timestamp queries worked; WebGPU GPU p95 was 2.52 ms in the synthetic street performance case.

The tile suite was bounded after the four-minute parked-car failure to preserve the brief's browser budget and release the slot for a waiting reference capture. Total browser usage was 23.3 of 30 browser-minutes. There are no remaining browsers or servers from these runs.

## Gate output

`scripts/dev/gate --fast --base main`: **GREEN**, 12.945 s after the final code/documentation changes and rebase. Lint, policy, types and selected unit stages passed; Chrome/deep stages remain advisory by project policy. Log: `$TURKU3D_WORK/reports/fast-gate.log`.

Resolver/argument/policy tests: 4 passed. Browser cleanup tests: 4 passed in 1.00 s. The host-scheduled production Vite build passed in 3.53 s after admission.

## Follow-ups

- Integrator/orchestrator: investigate `THREE.Object3D.add: object not an instance of THREE.Object3D. undefined` in all six render-material cases (both backends); their image/ID assertions passed before the console-error assertion. Reported to the renderer task and accepted by the orchestrator for follow-up.
- Integrator: investigate real-tile draw budgets: bridge 701/739 and office 642/605 versus the unchanged limit of 500; parked-car entry timed out at 240 s. Retain `reports/playwright-tile/` failure contexts. Complete the 23 unrun tile cases in advisory integration.
- `fix-fly-fallthrough` confirmed its unlanded probe already uses CfT with mock/basic flags and will replace its literal executable path with `chromeExecutable()` on rebase after this landing.
