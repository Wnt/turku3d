## Result: DONE

Implemented on wt/fix-pages-push-race. The runner lands this branch at turn end.

## What changed

- Both integration lanes and advisory notes use the data root's publish/pages.lock through remote publication and state updates.
- Pages fetches the remote head under the lock, checks its release identity and creates a commit with that head as parent. Normal pushes replace force pushes; Pages now retains commit history.
- Source ancestry and tile input time prevent older code, unrelated code or older/unknown tiles from replacing a newer known release. Legacy Pages subjects supply source identity during migration.
- A lock/non-fast-forward rejection triggers exactly one refetch, ordering check and rebuilt commit. Authentication and other failures are not retried.
- Existing integration pages results carry published/skipped status, retry count and skip details, or the existing error outcome. Advisory notes append only if their deployment is still current.
- Added fast fake-git ordering/retry tests and local bare-repository coverage for parentage, stale release rejection, state preservation and advisory notes. Updated the developer README.

## How to verify

From this sandbox checkout:

```sh
source ../sandbox.env
python3 -m unittest scripts/dev/tests/test_pages_order.py scripts/dev/tests/test_pages.py
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
```

No network publication is needed for these checks.

## Gate output

- Focused Pages tests: 22 passed, 14.151 seconds under concurrent host load.
- Fast gate: TOTAL GREEN 2.657 seconds (3.1 seconds including wait-cmd); publish-tests passed in 3.031 seconds in the preceding run and was cached in the final run.
- Initial runs identified Python formatting and the README line budget; both were corrected. All selected lint, policy, type and unit stages pass. Browser and deep stages were excluded as required.

## Follow-ups

None.
