## Result: DONE

Implemented on the fix-watch-tools branch; the Codex runner lands the DONE turn.

## What changed

- Removed scheduler locks and writes from watch/status/list reads. Watch previously called the
  scheduler's locking, pruning snapshot repeatedly from display properties. Liveness now uses
  one read-only process snapshot per locale per poll, including legacy localized identities.
- `watch --plain --once` replays eight recent sentences per file and exits. Startup reads bounded
  256 KiB tails and the two newest task event logs; `--replay N` changes the sentence limit.
  Live mode discovers tasks, resume logs and appended events. Git counts refresh only for live
  or explicitly selected tasks. Partial token totals are marked unknown; unread counts cover
  retained coordination history. These limits are documented in `scripts/dev/watch.md`.
- `codex-task ls` lists running tasks first and ten recent stopped/inbox tasks, plus an omitted
  count. `--running` selects only live attempts; `--all` includes every task. Compact listing
  never calls detailed status, per-task Git or full event scans. Detailed status also uses a
  bounded event tail and recorded worker finish time for idle duration.
- Added seven small synthetic regression tests and updated README commands. Extracted the
  monitor reference to keep the README below its existing 800-line policy budget.

Real task directory smoke checks on 2026-10-28: `ls` exited in 0.193 s, `ls --running` in
0.202 s, and `watch --plain --once` in 1.434 s. Output is retained under
`$TURKU3D_WORK/reports/watch-tools/`. A synthetic PTY running the default curses mode showed
one live task in 0.204 s, displayed an appended event in 0.248 s, and quit with exit 0;
its transcript and measurements are under `$TURKU3D_WORK/qa/watch-live/`. These are observed
smoke-check measurements, not timing assertions in tests.

## How to verify

```sh
source ../sandbox.env
scripts/dev/codex-task ls
scripts/dev/codex-task ls --running
scripts/dev/codex-task ls --all
scripts/dev/watch --plain --once
scripts/dev/watch                         # q quits; append events from another terminal
scripts/dev/codex-task wait-cmd -- python3 scripts/dev/tests/test_watch.py
scripts/dev/codex-task wait-cmd -- python3 scripts/dev/tests/run.py test_tooling.py
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
```

## Gate output

Fast gate: `TOTAL GREEN 18.831s` after rebasing onto c0c06f4d; final verification was
`TOTAL GREEN 1.689s` with unchanged stages served from the gate cache. All selected fast stages passed;
deep and browser stages were skipped as specified by the gate. The initial run caught the README
line budget; splitting the monitor reference fixed it without changing the policy.
Monitor tests: 29 passed in 2.274 s. Existing tooling tests: 14 passed in 21.31 s.

## Follow-ups

None.
