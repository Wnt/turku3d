# Codex briefs: waves and dependencies

The orchestrator (one Claude session) launches each brief as a Codex task (gpt-6-astra, high effort) in its own
sandbox and lands the branches one at a time. Every brief starts from [COMMON.md](COMMON.md).

## Wave 0: MVP tooling (runs first, in parallel)

| Brief | What | Blocks |
|---|---|---|
| [W0-A](W0-A-agent-tooling.md) | sandbox isolation (`wt`), `codex-task`, `here`, AGENTS.md | the clean way to launch and land Wave 1 |
| [W0-B](W0-B-quality-gate.md) | fast selective gate, hooks, flag registry, dead-code cleanup | "green before done" for Wave 1 |
| [W0-C](W0-C-mac-pipeline.md) | pipeline env on the Mac, `work-3x3` fetch, local `make tile` | W1-01..05, W1-10, W1-12 |
| [W0-D](W0-D-coordination.md) | inbox, shared board, `codex-task msg` for agent coordination | ad-hoc coordination of running agents |

## Wave 1: playable office block (Phase 1, as parallel as possible)

| Brief | Area | Starts | Needs from others |
|---|---|---|---|
| [W1-06](W1-06-character-input.md) | input system + Xbox gamepad + character, stairs, foot IK | immediately | — |
| [W1-07](W1-07-vehicle.md) | car handling + curb-strike + gamepad driving | immediately | W1-06 action names |
| [W1-08](W1-08-rendering.md) | lighting, shadows, post-processing, perf instrumentation | immediately | — |
| [W1-09](W1-09-facade-textures.md) | photo-referenced facade textures with Codex | immediately | W1-03 facade list (interim path until then) |
| [W1-11](W1-11-visual-qa.md) | 20 reference views + Astra judge + regression score | immediately | real tiles for the final run |
| [W1-01](W1-01-ground-curbs.md) | ground layer with real curbs | after W0-C | — |
| [W1-02](W1-02-structures.md) | stairs, walls, fences, ramps, quays | after W0-C | W1-01 quay classification |
| [W1-03](W1-03-buildings.md) | building shells, collision, facade UVs | after W0-C | — |
| [W1-04](W1-04-assembler.md) | layer contract, assembler, packaging, streaming robustness | after W0-C | lands the layer API early |
| [W1-05](W1-05-validator.md) | validator: seams, real tiles, car checks | after W0-C | W1-07 car module |
| [W1-10](W1-10-ground-textures.md) | ortho drape + ground material library | after W0-C | W1-01 material IDs |
| [W1-12](W1-12-e2e-playtest.md) | `make play`, scripted playtests, report | after W0-C | everything, for the final run |
| [W1-13](W1-13-lidar-toolkit.md) | lidar toolkit + lidar-first curb and relief detection | immediately | W1-01 breakline interface |

Briefs that start before Wave 0 lands work without `scripts/dev/gate` and AGENTS.md: they use the area `make *-test`
targets and rebase on `main` when told to.
