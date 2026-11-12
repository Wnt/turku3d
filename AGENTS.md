# Turku3D agent policy

The rules every agent follows (2026-11-12). Procedures live in the linked documents: read one when your task touches
it, not up front. Change a rule here, in place. Earlier flows are archived in `docs/archive/` and do not apply. Write
code, comments, documentation, commits and reports in English.

**Goal:** a GTA-style open world of Turku in desktop Chrome:
- three.js r186 (WebGPU);
- Rapier 0.21 in a physics worker;
- Vite;
- an Xbox Elite Series 2 controller.

Visual fidelity at street level comes first: game stills must be recognisable as the real Turku
([visual brief](docs/design/visual-brief.md)). The world is being rewritten as one pipeline of entities, a
content-addressed store and location manifests ([world pipeline](docs/design/world-pipeline.md)); its tracks are
`w2-core`, `w2-buildings`, `w2-observe`, `w2-street`, `w2-look` and `w2-eval`. No backward compatibility is needed:
fix things at the layer where it makes most sense.

## Where to look

| When you… | Read |
| --- | --- |
| start: system, state, world frame, formats | `docs/ARCHITECTURE.md`, `docs/STATUS.md`, `docs/specs/coordinates.md`, `docs/specs/` |
| work on the world: entities, descriptions, store, manifest, generators, live update | [docs/design/world-pipeline.md](docs/design/world-pipeline.md); buildings: `docs/specs/building-description.md` |
| set up a sandbox, worktree or ports, or look for a command | [scripts/dev/README.md](scripts/dev/README.md) |
| merge, choose evidence, publish to city.lab | [docs/DEVELOPING.md — Agent flow](docs/DEVELOPING.md#agent-flow) |
| capture or score visuals | [docs/DEVELOPING.md — Appearance loop](docs/DEVELOPING.md#appearance-loop), `scripts/dev/scene-capture.mjs` |
| launch or drive a browser | [scripts/dev/browser.md](scripts/dev/browser.md) |
| add or publish an asset or image | `game/public/assets/LICENSES.md`, `docs/published-images.toml` |

## Never

- **Merging and deploying:**
  - Never merge into `main`, push, run `codex-task finish` or `land`, or deploy to Pages or OCI.
  - `city.madekivi.fi` is frozen at `63b0b015`: no uploads, tile or scenery exports, or soundboard publication. The
    OCI export units and their credentials stay retired.
  - Experiments go only to `city.lab.madekivi.fi`.
  - Local generator changes need no approval; a production rollout is Jonni's decision.
- **Machines and processes:**
  - Never use Jonni's Chrome profile or ports 5171/5175, or any browser except Chrome for Testing through
    `tools/dev/chrome-args.mjs`.
  - Never kill processes you did not start, stop other agents' servers, or use `pkill -f`/`pgrep -f` with a pattern.
  - Use only your own ports (`TURKU3D_PORT_BASE` + 0..9), worktree, work directories and browser profile.
- **Services and data:**
  - Never use paid services or new API keys.
  - Never print or commit secrets; they stay in `~/.config/turku3d/`.
  - Never write to `~/turku3d/data/raw`.
- **What goes into Git:** nothing from GTA V, Google 3D, Street View, Look Around, Mapillary, Bing imagery or
  panoramas, and no raw city data. Assets use CC0 first, or CC-BY with attribution.
- **Blender:** only through the Blender MCP tools.
- **Feature flags:** none, except measured A/B alternatives in `docs/flags.toml` with an owner, a reason and removal
  within ten merges. Delete code you replace.
- **The old world:** never build on the Gen 1 tile generator (`pipeline/turku3d/coarse`), its caches or the
  prepared-scene compiler (`pipeline/turku3d/scene`). Reuse source-data readers and copy good algorithms.
- **One-off code:** no building ids or coordinates in code. Everything specific to a building is data in its stored
  description, and the same commands work at every location.
- **Tests:**
  - Never weaken an assertion to make a test pass.
  - Never spend time on slow checks:
    - Select single test cases, not whole suites or modules.
    - Do not run them yourself before `merge.py`, which runs them once.
    - Anything slow goes to `--async-test`.
    - A judge run is never part of a merge or deploy. It is only for a visual milestone, started in the background.

## Always

- **Your own branch.** Work on your own `local/*` branch in your own worktree.
- **Merge.**
  - When the tests you selected pass, commit and merge into `local/loop-integration`.
  - Milestones are stills: deploy to city.lab, smoke and make the post-deploy run only when you changed the deployed
    client in a way city.lab needs.
  - Never ask whether to commit or merge.
  - Fix forward; no rollbacks.
  - Merge at least every 45 minutes while something works.
- **Stills first.**
  - A milestone is judged on stills from `scene-capture.mjs --mode render`. Collision, physics, streaming and
    multiplayer work is not required for one; do not design them out.
  - Performance is tracked, not enforced: the capture records frame time on Jonni's M1 Max at medium quality,
    triangles and texture memory per location. The 30 FPS average is the target they are read against.
- **Hard limits.**
  - Keep at least 25 GiB free on osgallery-dev, and delete your own scratch, builds and caches.
- **Keep what was learned.**
  - Preserve measurements, authored observations, AI facade descriptions, facade-kit results, the texture library
    and reusable assets.
  - Write what a vision model learns about a facade back into the building data.
  - Replace a weak generated asset, texture or description when the new one is better.
- **Reference imagery.** When a reference photo is missing, old or shows the wrong state, fetch newer panoramas with
  `python -m turkutex.streetview.panos` ([qa/README.md](qa/README.md#the-reference-views)). It takes a minute or
  two.
- **Commit trailer.** Claude commits end with `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`; Codex
  commits end with `Co-Authored-By: Codex (gpt-6-astra) <noreply@openai.com>`.

## Reports

Keep reports short: result, evidence, next. A run result uses these headings:
- `## Result: DONE` or `## Result: BLOCKED`
- `## What changed`
- `## How to verify` (exact commands)
- `## Gate output` (selected checks and timing)
- `## Follow-ups` (concrete remaining work only)

Report an unmet visual goal plainly. A deployment alone is not success.
