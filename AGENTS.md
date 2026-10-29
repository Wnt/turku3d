# Turku3D agent policy

Use English for code, comments, documentation, commits, progress updates and final reports.

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
