## Result: BLOCKED

The requested fix is committed, but the fast gate fails on missing voice assets.

## What changed

Registered facade_kit.test.mjs as a knip entry and added it to package-js-tests alongside prop_atlas.test.mjs. Existing regression assertions are preserved.

## How to verify

```sh
source ../sandbox.env
node --test pipeline/turku3d/package/prop_atlas.test.mjs pipeline/turku3d/package/facade_kit.test.mjs
npx knip --no-progress --include-entry-exports
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
```

## Gate output

Package JS tests: 2 passed (1.24 s). Knip: passed (9.4 s).
Fast gate after rebasing onto main 83339407: RED (29.569 s).
Failing command: scripts/dev/gate --fast --base main.

```text
FAILED tests/test_committed.py::test_committed_clips_are_current
Left contains 50 more items, first extra item: 'address_brahenkatu_1: no clip'
1 failed, 17 passed in 7.19s
```

## Follow-ups

Orchestrator request f2c35a906e6c4948a36b382ab8df46d6 asks the voice asset owner to regenerate the 50 missing address clips required by the published address pools. Rerun the fast gate after those assets land.
