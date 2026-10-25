# W0-D: Agent coordination: inbox, shared board, `codex-task msg`

Read `AGENTS.md` (and `docs/briefs/COMMON.md` §3–4). You run in your own sandbox.

## Why

`codex exec` is non-interactive, so the orchestrator cannot add instructions to a running agent, and parallel agents
cannot tell each other about interface changes. Today the only channel is stop + resume. Add a lightweight,
file-based channel that agents check between work steps. It gives ad-hoc coordination without routing everything
through the orchestrator, while the orchestrator still sees every message.

## Deliverables

1. **Inbox per task**: `~/turku3d/tasks/<name>/inbox.jsonl`, append-only. Each entry has `{id, time, from, to,
   kind: note|request|interface|warning, subject, body, refs: [commit|path], ack: null}`. Acknowledging writes a
   separate `ack` record that references the id; nothing is edited in place. Writes are atomic and locked.
2. **Shared board**: `~/turku3d/coord/board.jsonl`, using the same record shape with `to: "all"`. Use it for interface
   announcements ("layer API landed at <commit>"), cross-area requests and warnings.
3. **CLI** in `scripts/dev/codex-task`, reusing its locking and paths:
   - `msg <name|all> --kind K --subject S [--ref R] "body"`: writes a message; `from` defaults to `$TURKU3D_SANDBOX`
     or `orchestrator`.
   - `inbox [<name>] [--unread] [--json]`: reads messages; inside a sandbox it defaults to that sandbox's own inbox.
   - `ack <id> [--note "..."]`
   - `board [--since N] [--json]`
   - `ls` and `status` show an unread count per task.
4. **Runner integration**: `run` and `resume` add a short "Coordination" block to the prompt explaining the commands.
   When a task finishes, `ls` flags unacknowledged messages, and `land` refuses while the task has unacknowledged
   `request`/`warning` messages addressed to it, unless `--force`.
5. **Policy** in `AGENTS.md`, kept short:
   - Check `scripts/dev/codex-task inbox --unread` and the board after each major step (each commit) and before
     finishing.
   - Acknowledge what you act on.
   - Announce interface changes others depend on (schemas, file layouts, module APIs) on the board with the commit.
   - Before changing another area's interface, post a `request` to its owner (when that task is running) or the
     board. Never edit another sandbox's files. Reading other worktrees is allowed, to see in-progress interfaces.
6. **Tests**:
   - concurrent writers (no lost or interleaved lines)
   - unread/ack logic
   - land refusal with an open request
   - prompt block injection

   Keep them fast, under the gate's time budget.

## Acceptance

- Two sandboxes exchange a request and an ack through the CLI. The board shows an announcement.
- `ls` shows unread counts, and `land` refuses while a request is unacknowledged.
- `scripts/dev/gate` is green, and the README in `scripts/dev/` documents the commands.
