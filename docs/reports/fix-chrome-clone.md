## Result: DONE

## What changed

All Chrome launch sites in this checkout use `tools/dev/chrome-args.mjs`, including
all six game Playwright configs, music-lab, QA rendering, compare/video capture,
game diagnostics (including the newly landed latency probe), texture-sanity, and Python-embedded placement/props previews. The gate and
integrator delegate to these configs; localhost does not start Chrome itself.

`chromeArgs` consolidates feature switches and adds `MacAppCodeSignClone`.
Playwright launch options remove its original default feature switch using
`chromeIgnoreDefaultArgs`, preserving every default disabled feature in the merged
switch. A compatibility test checks the pinned list against installed Playwright.
The helper supports both `--disable-features=value` and two-argument switches.

An always-on, uncached fast-gate policy parses launch sites/config overrides, follows shared
argument constants, checks embedded Node previews and native CDP launchers, and
rejects missing protection. Synthetic tests cover missing arguments, multiple launch
sites, defaults, merging, and missing Playwright default-switch suppression.
The parser uses an explicit root TypeScript dependency; npm's optional dependency
lock entries were reconciled and a clean `npm ci` verified.

## How to verify

From this sandbox checkout:

```bash
source ../sandbox.env
node tools/dev/check-chrome-launches.mjs
node --test tools/dev/chrome-args.test.mjs
python3 scripts/dev/host_slots.py browser -- node tools/dev/verify-chrome-clone.mjs
GATE_PYTHON=/Users/wnt/turku3d/envs/pipeline/bin/python scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
```

The real macOS probe passed native Chrome CDP (Chrome 156.0.8078.48), then the
music-lab `tests/music.spec.ts` Playwright spec. Sampling every 50 ms throughout
both launches found zero new `code_sign_clone.*` directories: 26 before, 26 after.
CDP verified the flag in `Browser.getBrowserCommandLine`; the Playwright debug log
shows one merged feature switch. Evidence is retained under
`$TURKU3D_WORK/reports/chrome-clone/{verification.json,probe.log}`.
Existing clones were only read; none were deleted by this task. A deliberate
unprotected-launcher edit was also rejected by a previously green fast gate;
the original was restored. Evidence: `reports/chrome-clone/policy-negative.log`.

Earlier attempts observed global clone creation that was not attributed to a
specific process. The final probe rejects any new directory, including transient ones;
it does not filter out directories attributed to other processes.

## Gate output

Fast gate **GREEN**, 12.495 s after the final rebase and launcher changes (21
passing stages, including the uncached launch policy and three synthetic merger /
default compatibility tests). Browser and deep stages were skipped as required.
Lint, types, Python/JS units and tooling passed. The earlier README line-budget
overrun was corrected without changing the limit.

## Follow-ups

Existing clone cleanup remains with the orchestrator and Jonni's approval.
