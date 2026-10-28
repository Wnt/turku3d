# Live agent monitor

Run commands from the repository root:

```bash
scripts/dev/watch                         # curses channels for all tasks
scripts/dev/watch w1-01-ground tool-watch # restrict task/inbox channels
scripts/dev/watch --plain                 # stream human sentences; also automatic for pipes
scripts/dev/watch --plain --once          # replay recent task/coordination events and exit
scripts/dev/watch --plain --once --replay 10 # limit startup sentences per file
python3 scripts/dev/tests/test_watch.py   # fast synthetic fixtures, no terminal/model calls
```

Channel 0 aggregates every displayed event. Each task has a channel, with separate `inbox:<name>`,
`board` and `landings` channels. New tasks and `events-*.jsonl` resume logs are discovered live.
Commands, exit results, edits, messages, gate summaries and runner output become short sentences.
Both live and `--once` start with eight sentences per file; `--replay N` overrides this within
a bounded 256 KiB tail per file. Only the two newest event logs per task are opened. `--once`
performs one snapshot and exits; it never waits for a scheduler lock. Codex events without
timestamps show read time, so replay timestamps are approximate;
coordination and landing records retain their recorded timestamps.

Keys: **left/right** cycle channels, **0–9** jump, **PgUp/PgDn** scroll, **End** follows again,
**l** toggles the selected task's `last.md`, and **q** quits. Stars indicate unseen monitor output,
independently of persisted inbox acknowledgements. The selected channel remains visible in the bar
when there are more channels than fit the terminal. `last.md` refreshes when its contents change.

The task status line shows state, elapsed time for the current attempt, commits ahead of local `main`,
unread inbox messages, and reported tokens. A live attempt **over 30 minutes** displays `OVER 30m`
with a highlighted status bar; the aggregate bar also highlights overdue tasks. Completed elapsed time
stops at `finished`. Liveness uses the runner's PID plus process start time, including compatibility
with existing localized macOS timestamps. New identities use the C locale. Git counts refresh every
five seconds for live or explicitly selected tasks; other stopped tasks and missing repositories
show `+?`. Task liveness is checked with one process snapshot per locale per poll; the scheduler
state is read without locking, pruning or writing it. Poll interval defaults to one second (`--interval N`).

Tokens sum input plus output from completed turns in the retained recent tails. Cached input and
reasoning output are subsets and are not added twice. The recent tail of the W2 ledger
(`~/turku3d/ledger/codex-calls.jsonl`, override `TURKU3D_CODEX_LEDGER`) supplies task-job totals
deduplicated by `call_id` and excluding retained voided calls; the larger event/ledger total wins.
The source is shown explicitly. Skipped history or missing usage is marked `+ unknown`; totals
are partial when history is skipped. Coordination unread counts cover retained messages and
acknowledgements. An unfinished first turn can show zero reported tokens. This is usage reported
so far, not an estimate of an in-flight turn.
Board unread counts belong to `TURKU3D_SANDBOX` (or `orchestrator` outside a sandbox).

The monitor is strictly read-only: no locks, acknowledgements, state changes, Python bytecode files,
agent input or signals. Files are followed by byte offset; partial UTF-8/JSON lines wait for a newline,
and truncation/replacement resets the affected reader. Renamed event archives preserve their offsets.
It uses stdlib Python and the shared `sandbox.py`, `task_runner.py` and coordination helpers, without
calling quota APIs. Storage overrides are `TURKU3D_TASKS_ROOT` (or `--root`), `TURKU3D_COORD_ROOT`,
and `TURKU3D_WT_ROOT`; overriding `--root` alone changes only the task source.
Implementation and tests adapt the read-only gta6 watcher reference, dropping dispatch/Linear and
Claude-specific concepts. The registered `watch-tests` gate stage exercises the terminal-free Monitor.
