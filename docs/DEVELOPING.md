# Developing Turku3D

[Project overview](../README.md) · [Documentation index](README.md)

Turku3D has three main runtime parts: **browser game** (`game/`), **multiplayer server** (`server/`), and an **on-demand city-data / tile pipeline** (`pipeline/`, `tools/`). The deployed game and generated world are separate systems.

## Quick start: browser client

A recent Node.js and npm are required; **Node 24** is used by the multiplayer server. The native geographic pipeline has additional, macOS/Apple Silicon-focused dependencies documented separately.

```sh
git clone https://github.com/Wnt/turku3d.git
cd turku3d
npm ci --prefix game
make game-dev
```

Open **http://localhost:5170/?scene=dev** for the synthetic development scene. `make game-dev` launches Vite and the local coarse-tiles service. The root Makefile defaults to port 5170; worktree sandboxes assign their own `TURKU3D_PORT_BASE`. On a development worktree, source its `sandbox.env` before running project tools.

Without the separately provisioned source-data tree and live generator, **do not assume the default Turku scene works locally**. A successful synthetic-scene launch does not verify real-city data.

```sh
make game-test    # Vitest unit tests
make game-build   # TypeScript check and production bundle
```

For affected browser checks, see [game/README.md](../game/README.md), [Playwright configuration](../game/playwright.config.ts), and [the agent flow](#agent-flow). Do not treat an unrun full browser suite as passing.

## Multiplayer server

```sh
npm ci --prefix server --ignore-scripts
npm test --prefix server
node server/index.js
```

The server defaults to loopback `127.0.0.1:7700`, not a publicly exposed endpoint. Its HTTP health route is `/healthz`. For an isolated run use `TURKU_MP_PORT=0` and a temporary `TURKU_MP_SCORES` directory. See [server setup](../server/README.md) and [network protocol](../server/PROTOCOL.md). The public service also depends on a separately configured proxy/tunnel.

## Real Turku world and data pipeline

The generated game world uses City of Turku open data (lidar, buildings, streets and orthophotos) and OpenStreetMap. **Bulk source data, generated tiles and secrets are not part of Git.** Shared inputs normally live under `$TURKU3D_DATA/raw` and each sandbox writes to `$TURKU3D_WORK`.

The real-data tooling includes Python 3.12/`uv` and native geospatial dependencies. Start with [architecture](ARCHITECTURE.md), [source manifests](../data-manifests/sources.yaml), [pipeline environment](../pipeline/env/README.md), and [developer operations](../scripts/dev/README.md) rather than running old one-off AOI build recipes. `make help` enumerates the current targets.

Generated tile content is requested on demand in production. Pages does **not** ship a fixed, full-city tile bundle. Consult the linked runbooks before publishing, warming, or operating remote services.

## Appearance loop

Visual work on a prepared scene (`office`, `port-arthur`) runs in one long-lived local session. A material or asset
change does not restart the game: the camera, physics, collision, ground, lighting and unchanged GPU objects stay.
The current goal is in the [visual brief](design/visual-brief.md).

1. **Start once.** `source ../sandbox.env`, run Vite (`cd game && npx vite`; it serves `scenes/shared/<sha256>.*`
   straight from the prepared asset store, `$TURKU3D_DATA/prepared-assets/shared` or `TURKU3D_PREPARED_ASSETS`), then
   `scripts/dev/preview-session.mjs start`. The session opens `/?solo=1`: the real game and renderer without the
   multiplayer bootstrap, straight into the scene (local hosts only; public builds keep the Multiplayer start).
2. **Try live** in the page (CDP or DevTools):
   ```js
   const road = __dev.scene.material('road');
   await __dev.scene.patch({ schema: 'turku-scene-appearance/1', scene: 'office',
     materials: [{ material: { ...road, roughness: 0.55 } }],
     assets: [/* { entityId, url: 'shared/<sha256>.glb', sha256 } */] });
   await __dev.scene.restore();   // back to the published appearance
   ```
   A patch replaces complete material records (same ids) and asset models by entity id; textures and models already
   loaded are reused. It refuses geometry, collision or lighting changes, and `uvScale` (publish that instead).
3. **Compare** alternatives in the same session: `scripts/dev/scene-capture.mjs --variant NAME=patch.json ...
   --baseline DIR`. Each variant is applied, captured in every view and restored; the manifest records how long each
   took to become visible.
4. **Publish to the scene** files:
   `bash pipeline/env/run.sh python -m turku3d.scene.appearance --scene game/public/scenes/office.scene.json --recipe pipeline/turku3d/scene/recipes/office.json --patch patch.json`
   updates the scene JSON, its recipe rows and the region catalog in place (`uvScale` allowed; only that material's
   UVs change) and checks that nothing else changed. `__dev.scene.reload()` then shows the published file in the
   running session. A geometry or lighting change needs the full compile (`python -m turku3d.scene`, which prints
   per-stage timings) and a page reload.
5. **Score a visual milestone** with `scripts/dev/scene-judge.mjs run --label "what changed"`.
   - **When:** only for a visual milestone. Never for every edit or merge, and never for a change with no visible
     difference.
   - **How:** start it in the background and carry on. It takes about 2 minutes. Never wait for it or gate anything
     on it.
   - **What it does:** it captures the gameplay views and the reference-photo views with the scene alone
     (`scene-capture.mjs --mode render`). The Fable 5.2 visual judge then scores them blind in six categories, twice
     in parallel. The overall score goes into the shared history, and `scene-judge.mjs report` shows the change per
     milestone. Rubric: [scripts/dev/judge/JUDGE.md](../scripts/dev/judge/JUDGE.md).
6. **Ship:** commit, merge ([agent flow](#agent-flow)), then `tools/share/deploy.sh --scenes-only` when the renderer is
   unchanged (a full deploy otherwise). Capture city.lab with `scene-capture.mjs --base https://city.lab.madekivi.fi/`
   and play the public Multiplayer entry once, since the solo session skips it.

Measured on 2026-11-12 (M1 Max, `office`, 160 MB scene JSON):

| Step | Time |
| --- | --- |
| Live material patch until visible in a fixed view | 2.2–2.5 s (apply 1.8–2.1 s, 24 quiet frames 0.4 s) |
| Restore to the published appearance | 2.0 s |
| `__dev.scene.reload()` with nothing changed | 0.6 s |
| Solo session boot until playable (once) | 36 s |
| Capture: 3 views, 1 variant × 3 views, 4 s walk, compare sheet (live session) | 14 s |
| Same capture of city.lab (`d3e2021d`, no readiness signal: fixed 6 s settle per view) | 38 s |
| One-material publication (`turku3d.scene.appearance`, scratch copy) | 8.2 s (read and seal check 5.1, apply 0.2, write 2.9) |
| Full office compile, cold | ≈208 s (ground 135, bakes 26, finishes 13, collision 13) |

`deploy.sh --scenes-only` prints its phase timings; it has not run against osgallery-dev yet.

## Agent flow

The rules are in [AGENTS.md](../AGENTS.md); this is how agents carry them out.

**Sandbox.** Create isolated work with `scripts/dev/wt new NAME --from local/loop-integration` and source
`../sandbox.env` before tools:
- `TURKU3D_DATA` is shared input;
- `TURKU3D_WORK` holds your writable products;
- `TURKU3D_BROWSER_DIR` holds your browser files;
- `TURKU3D_PORT_BASE` reserves ten ports: dev +0, dist +1, QA +2, probes +3..+9.

The preview session's CDP port is assigned by the OS. Commands are in
[scripts/dev/README.md](../scripts/dev/README.md).

**Slice: merge, deploy, smoke, one run.**

1. **Merge** as soon as the tests you selected for the changed area pass. They run in parallel under one hard
   30-second budget; a failure or timeout leaves integration unchanged:
   ```sh
   python3 ~/turku3d/tools/codex-loop/merge.py AREA SHA... --what TEXT (--test CMD ... | --no-tests-reason TEXT)
   ```
   Examples of `--test`:
   - `'cd game && npx vitest run tests/unit/<file>.test.ts'`
   - `'python3 scripts/dev/tests/run.py <file>.py'`
   - `'bash pipeline/env/run.sh python -m pytest -q <file>'`

   - `merge.py` runs the selection once. Do not run it yourself beforehand.
   - Select single cases (a test name, `-k`, a grep filter), not whole modules.
   - Anything that takes more than a few seconds goes in `--async-test CMD --async-reason TEXT`. Those start after the
     merge and never block it.
2. **Deploy** right after the merge, package and upload only:
   ```sh
   python3 ~/turku3d/loops/night-coord/coord.py run --by NAME deploy -- ~/turku3d/wt/loop-integration/repo/tools/share/deploy.sh
   ```
   A scene or material change with an unchanged renderer uses `deploy.sh --scenes-only` (no build, no restarts).
3. **Smoke** with curl only, in at most five seconds, reading the reported health rather than only HTTP 200:
   `python3 ~/turku3d/wt/loop-integration/repo/tools/share/smoke.py SHA`. A red smoke is fixed forward.
4. **One short selective run** of your own on the deployed system, started in the background, with a hard limit of
   ten minutes including lease waits. Read `~/turku3d/loops/board.md` first for what others ran. Then run the
   narrowest check of what the change affects:
   - **a generator change:** build only its affected tiles and check outcomes, timings and counts against the source;
   - **a client change:** exercise the feature on the Air or Windows;
   - **a visual change:** one `scene-capture.mjs --base https://city.lab.madekivi.fi/` comparison (under a minute),
     and one go at the public Multiplayer entry.

   No judge runs and no suites. Skip this step for a change with nothing to exercise on the deployed system, such as
   documentation. At most one forward fix, then report to the coordinator.
5. **No rollbacks.**
   - Commit often.
   - While something works, merge and publish at least every 30 minutes, bundling related visible changes. No
     finished work waits unmerged.
   - Write one line per merge and deploy to `~/turku3d/loops/board.md`.
   - There is no agent register, heads-up or acknowledgement ceremony.

**Evidence:**
- Use the lightest relevant evidence and select the affected tests yourself, with grep or name filters. Never run a
  suite blindly or treat a generic suite as evidence for a change it does not exercise.
- Stills, drives, tile builds and judge runs are never pre-merge gates.
- Visual evidence comes from the one capture command, `scripts/dev/scene-capture.mjs`: view sets, live variants,
  baselines, the walking clip, checks and timings. Extend it instead of writing per-feature capture, check or probe
  scripts.
- Make one comparison and walking clip per integrated visual milestone. Keep narrow diagnostics private unless they
  explain a problem.
- Score visual progress with `scripts/dev/scene-judge.mjs`, only for a visual milestone and in the background. A
  gain within the judge's measured noise, about 5 points, is not a gain
  ([scripts/dev/README.md](../scripts/dev/README.md)).
- Require byte identity only for components intentionally outside an edit.
- Use content hashes per generator stage, tile and scene, so that only affected parts rebuild, on demand where
  possible.

**Browser:** see [scripts/dev/browser.md](../scripts/dev/browser.md):
- Chrome for Testing through `tools/dev/chrome-args.mjs`;
- one preview session per agent, patched at runtime (`window.__game`, `window.__dev`, `__dev.scene`) instead of
  reloaded, with URL parameters only for what the initial load needs;
- `?solo=1` for local appearance work;
- an exclusive `host_slots.py` lease and a fresh launch for performance evidence.

## Source and deployment boundaries

| Path | Responsibility |
| --- | --- |
| `game/src/` | Browser engine, renderer, input, physics, gameplay, UI and network client |
| `server/` | Multiplayer presence, shared vehicle state, hourly scores |
| `pipeline/` | Source-data processing, materials and geographic generation |
| `tools/` | Local and remote tile services, developer utilities |
| `scripts/dev/` and `mk/` | Isolated worktrees, test selection, builds and publication |
| `docs/specs/` | Data and coordinate contracts |
| `docs/reports/` | Dated test and research findings |

The released client at [wnt.github.io/turku-game](https://wnt.github.io/turku-game/) (deploy-only [Wnt/turku-game](https://github.com/Wnt/turku-game) repository) and city.madekivi.fi are frozen. Next-generation work is published only to city.lab.madekivi.fi with `tools/share/deploy.sh` after it is merged into `local/loop-integration`. A PR does **not** publish anything.

For small changes, run only relevant tests and explain what you did and did not verify. The project's rules are in [AGENTS.md](../AGENTS.md) and the merge and deploy flow is in [Agent flow](#agent-flow); that workflow assumes the owner's configured data, machines and credentials.
