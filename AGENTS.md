# Turku3D agent policy

Use English for code, comments, documentation, commits, progress updates and final reports.

## Local Gen 2 phase (from 2026-11-02): this section overrides the merge/deploy flow below

- The published game and its tiles are frozen. Never merge into `main`, never push, never run
  `codex-task finish` or `land`, never deploy to Pages or OCI. Work on your own `local/*` branch; kept work is merged
  into `local/loop-integration` (by you or the integrator).
- Move fast, break things, move forward only (no rollbacks). Think outside the box: fix or improve at the layer that
  makes most sense (client, generator, tile format, data, assets, tooling). No backward compatibility is needed; the
  Gen 2 target design is `~/turku3d/plans/gen2-architecture.md`.
- Ambition: visual fidelity rises significantly. Street level, where players spend their time, comes first.
- Choose your own evidence: tests, same-pose renders, the visual judge (`~/turku3d/tools/visual-judge/judge.py`),
  tile builds, the bench probe. No fixed time budgets, smoke runs, post-deploy runs, agent register, heads-up or
  acknowledgement ceremony. Shared notes go to `~/turku3d/loops/board.md`.
- Iterate in a long-lived browser session instead of reloading the game: keep one session per agent alive (close it
  after at most ~2 h idle), connect over the Chrome DevTools Protocol, and switch options and patch code at runtime
  (a shared dev API on `window.__game`/`window.__dev`, Vite HMR, monkey-patching). Prefer runtime toggles over new URL
  parameters, because the game's startup is slow; URL parameters only for things needed at initial load.
- Local generator changes need no approval; only a production rollout does (Jonni decides).
- Buildings: improve the data of all buildings and make generation scale to the whole city. Every time a vision model
  looks at a facade, write what it learns back into the building data; reuse earlier vision/facade work that hasn't
  been fully used yet. Replace existing facades, textures and AI descriptions whenever the new result is better.
- Caching: keep content hashes, and aim for fine-grained, per-area rebuilds of geometry, textures and assets that
  happen on demand (when a player views an area whose latest version isn't cached yet).
- Delete code you replace instead of flagging it. Reports are short: result, evidence, next.
- Hard limits that stay:
  - An average of 30 FPS on Jonni's M1 Max at medium (optimise further later).
  - Multiplayer is not a hard limit: keep collision geometry and shared world data structured so it can be made
    multiplayer-compatible later if it isn't from the start.
  - Your own ports and browser profile; never Jonni's Chrome profile or ports 5171/5175; never kill processes you
    didn't start; don't stop other agents' servers.
  - No paid services or new API keys; secrets never printed or committed; `~/turku3d/data/raw` is read-only.
  - Nothing from GTA V, Google 3D or panoramas goes into the repo; assets use permissive licences (CC0 first, CC-BY
    with attribution in `game/public/assets/LICENSES.md`); Blender only through the Blender MCP tools.
  - Keep ≥ 25 GiB free on osgallery-dev; delete your own scratch, builds and caches when done.

## Goal and context

Turku3D is a GTA-style open world of Turku in desktop Chrome: three.js r186,
Rapier 0.21 in a physics worker, Vite and an Xbox Elite Series 2 controller.
The first playable office block is Läntinen Rantakatu 15, E 23459668 N 6704254.
Read `docs/ARCHITECTURE.md`, `docs/STATUS.md` and the brief. Coordinate contracts are
in `docs/briefs/COMMON.md` and `docs/specs/`.

The world is generated on demand by the coarse/draft generator as players move.
Players receive the latest generator version. There are no hand-built starting
tiles or background full-tile integration batches. Preserve existing AI facade
descriptions, facade-kit results and the texture library on their buildings.
Pages carries only the game; the world and assets come through the tunnel/OCI host.

## Merge, deploy and test flow

Jonni's flow for every slice; working rules 4–7 give the details and commands.

1. Before merging, run only the tests for the area you changed, chosen by you, at most
   30 seconds of wall time in parallel.
2. Deploy immediately after merging. Deploying is only packaging and uploading.
3. Smoke checks take at most 5 seconds, use curl only, and check that the main integration
   points are up **and healthy** (for example generator build outcomes, not just HTTP 200).
4. After deploying, run one bigger test run of your own with a hard 10-minute limit. It is
   really selective, exercises what your change affects on the deployed system, and takes
   into account what other agents ran after recent merges (`codex-task checks`). Forward
   only: at most one fix, then report to coordination.
5. No rollbacks. Grep for meaningful tests within the tight budget; never run a suite
   blindly or treat a generic suite as evidence for a change it does not exercise.

## Working rules

1. Own the outcome. Fix a problem where it belongs; remove replaced and dead code.
   Use plain git/npm/python/make when a convenience script breaks, fix its cause and report it.
2. No feature flags except measured A/B alternatives registered with an owner,
   reason and removal condition of at most ten merges. Remove the losing path.
3. Commit very often on your branch. Git shows the details of concurrent work.
   End each commit with `Co-Authored-By: Codex (gpt-6-astra) <noreply@openai.com>`.
4. The single active-agent register is `scripts/dev/codex-task agents` (or `--json`),
   refreshed into `wt/.registry/agents.json` with actual branch names. Use no additional
   overlap tooling. Send a short high-level heads-up only when a change clearly touches
   formats, data structures or code another active agent is changing.
5. Use the lightest relevant evidence. Select only affected tests yourself, including
   grep/name filters. All selected pre-merge commands run in parallel within one **hard
   30-second wall budget**. Fix rebase failures and rerun the relevant selection. The
   quick suite stays tiny; a new quick test needs a strong reason. Full tiles, area
   rebuilds, exhaustive renders and browser suites are not pre-merge gates.
6. Immediately after merging into main, deploy to Pages. Deployment is **package +
   upload only**: no tests, tile builds, deploy lock, queue/coalescing rules, verified-only
   releases, rollback or rollback preparation. OCI source publication has no build gate.
   Move forward with a new fix when needed. Publishing does not wait for an agent's
   host slot. Stay comfortably within ten Pages builds per hour by completing useful
   slices; do not implement a deployment queue.
7. After deployment, run curl-level smoke checks of the integration points, **five
   seconds total**, with no browser. Check that they are up and healthy: interpret
   `/coarse/health` rolling per-pass outcomes, latency and error kinds. Recent full-pass
   failures/cancellations or excessive p50 make smoke red; HTTP 200 alone is insufficient.
   Then start one agent-owned selective async run with a **hard ten-minute wall budget**
   including slot waits. Read `codex-task checks` before choosing meaningful evidence.
   Exercise what the change actually affects on the deployed system: generator changes
   build representative real tiles on deployed OCI (first + full), checking outcomes,
   timings and counts against source; client changes exercise the changed feature.
   The agent chooses tiles, commands and expectations; generic synthetic suites do not
   substitute for relevant deployed evidence. Frozen-source async runs survive rebase
   or sandbox removal. At most one forward fix is allowed (`finish --fix-for JOB`);
   report further work to the orchestrator. The sentinel and `checks` expose build health.
8. Only affected parts rebuild, using content hashes per generator stage and tile.
   Reuse existing assets and generated evidence. Inspect a representative changed
   instance when visual evidence is useful; do not make a new area build a quick test.
9. Use only your sandbox ports, work/cache/build directories and browser profile.
   Never edit another sandbox or kill processes you did not start. Never use `pkill -f`
   or `pgrep -f` with a pattern in your own command. Heavy optional builds/tests use
   `scripts/dev/host_slots.py`; game deployment bypasses admission queues.
10. Shared data is `~/turku3d/data`; `raw/` is read-only. No paid services or new API
    keys. Secrets stay in `~/.config/turku3d/`, never in the repo or printed. Codex
    image generation may use the weekly quota up to 100%; runners check it.
11. No Street View, Look Around, Mapillary, Bing imagery or raw city data in git.
    Main's tree is published publicly. Keep reference images and screenshots local;
    `docs/published-images.toml` records the source/licence of published images.

## Finish a slice

Start with `scripts/dev/here`, `scripts/dev/codex-task agents` and `git rebase main`.
Source `../sandbox.env` before tools. The exported `TURKU3D_DATA` is shared input;
`TURKU3D_WORK` owns writable products; `TURKU3D_BROWSER_DIR` owns browser files.
`TURKU3D_PORT_BASE` reserves ten ports: dev +0, dist +1, QA +2, probes +3 through +9.

Use `scripts/dev/wt new NAME --from main` for new isolated work. Work only in the
assigned checkout. A running agent can finish a slice and retain its sandbox:

```sh
source ../sandbox.env
scripts/dev/codex-task checks --last 10
scripts/dev/codex-task finish NAME --result "$TURKU3D_WORK/reports/result.md" \
  --keep-sandbox \
  --test 'python3 scripts/dev/tests/run.py test_delivery.py test_pages.py' \
  --async-test 'python3 scripts/dev/tests/run.py test_tooling.py test_caches.py' \
  --async-reason 'Delivery paths changed; these broader cases were not covered by recent runs'
```

Choose commands for your own affected area; the example is not a universal gate.
For a change with no affected quick tests, provide `--no-tests-reason` instead of
`--test`. `--heads-up TEXT` sends the shared-interface notice before rebase.

`finish` sends that notice, rebases, runs the selected checks in parallel with the
mandatory repository-wide policy and Chrome-launch policy stages within 30 seconds,
merges into main, announces the merge, packages/uploads Pages, updates changed OCI
source without a build gate, runs five-second health-aware smoke
probes and starts the selective async run. A merge conflict or red quick selection
leaves main unchanged. An upload or later test failure never rolls main back. The
runner's turn-end compatibility command uses the recorded selection; call `finish`
yourself for each green slice. Do not end DONE without selecting affected checks.

No blanket fast/deep gate is required. `scripts/dev/gate --fast --base main` remains
an optional routed check you may select when appropriate. Slow investigations belong
in the selective post-deploy run, not before merge. Do not weaken assertions to pass.

## Coordination and operation

Send a short message with `scripts/dev/codex-task msg TASK --kind note --subject TEXT BODY`.
When stuck, send a `request` explaining the blocker, attempts and needed input; continue
independent work. Handle inbox requests/warnings and acknowledge with `codex-task ack ID`.
Check `codex-task board` after commits and before finishing. Merge notices tell active
agents to rebase; inspect git for details. There is no file-ownership/overlap registry.

The orchestrator uses `codex-task run/resume/status/log/result/stop`. Stop only your own
process group. Remove finished sandboxes with `scripts/dev/wt rm NAME`; live processes
must stop first. `--force` discards local work but never kills processes. Legacy remote
osgallery-dev is retired. Commands: `scripts/dev/README.md`.

Long measurements go through `scripts/dev/codex-task wait-cmd -- COMMAND` or are polled
at 30-second intervals. Keep progress updates to completed stages and plan changes.

## Run result

End with `## Result: DONE` or `## Result: BLOCKED`, `## What changed`,
`## How to verify` (exact commands), `## Gate output` (selected checks and timing),
and `## Follow-ups` (concrete remaining work only). Use English under these headings.
