## Result: DONE

Ready for runner landing; no manual merge performed.

## What changed

Host GPU capacity is 3 workers; the integration reservation uses 2, reduced to 1
Chrome worker at load >= 60. CPU costs, lane limits, starvation protection, crash
recovery and Chrome-phase queueing remain unchanged. Playtest respects the assigned
worker budget instead of launching two workers inside a one-worker browser slot.

The reservation and host cap must change together: a four-GPU reservation cannot
fit a three-GPU host. Reducing CPU budgets to match GPU numbers would also break
the independent data lane's four-job child contract. Tests now assert 2/1 Chrome
workers and explicitly check spare capacity, Chrome-phase queueing despite that
capacity, and the data lane's lack of GPU reservation. Existing process tests,
including both previously reported failures, pass without relaxed assertions.

README numbers and timeout reasoning are updated. The advisory Chrome runner has
a two-hour timeout per invocation; the 120-second Playwright timeout is per test.
The reported 1.9-minute four-worker suite suggests roughly 3.8 minutes with two
workers (7.6 with one), before load overhead. No timeout increase is needed.
These are estimates; no real Chrome suite was run for this scheduler change.

## How to verify

```sh
source ../sandbox.env
python3 scripts/dev/tests/run.py test_host_slots.py
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
```

## Gate output

Scheduler tests: 12 passed, 0 failed in 3.01 s.
Fast gate: GREEN in 1.041 s (cached generated, JS lint, game types and playtest tools;
Python lint and policy passed). The initial uncached checks passed except for the
README line budget, corrected by condensing the changed section.

## Follow-ups

None. The runner lands this DONE branch at turn end.
