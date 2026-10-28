# Draft freshness (2026-10-29)

Requested outdated tiles now wait for a new build. The structural first pass is
served when ready, followed by the full upgrade. A complete last-good manifest
is served with `stale: true` only after a replacement fails. The response carries
a failure marker, service logs record the failure, and health exposes cumulative
build failures plus the current failed-tile count. Failed builds retry immediately
when their source/input key changes, otherwise after the existing backoff.

Raw-input identity now hashes content instead of paths, sizes and timestamps.
VRT identities include their referenced raster bytes, including in prepared-input
cache keys. Source identities already hash generator content independently of git;
existing stage caches reuse unaffected results. Scheduling edits remain excluded.

Startup warming still covers exactly six tiles: `t0_-1_0`, `t0_-1_1`, `t0_0_0`,
`t0_0_1`, `t0_1_0`, `t0_1_1`. This includes Auransilta. It yields to active player
demand and waits for real full builds; stale fallback and failed HTTP responses
produce a failed `prebuild.json` record and a failing process exit.

## Verification

The original slice `bae48742ce57` merged with selected checks in 1.439 seconds:

```sh
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_latency.py pipeline/tests/test_stage_cache.py pipeline/tests/test_coarse_demand.py
bash pipeline/env/run.sh python tools/remote-drafts/test_public.py
python3 scripts/dev/tests/run.py test_deploy_drafts.py
```

35 Python tests, five public HTTP tests and three deployment tests passed, with
repository policy and Chrome-launch policy checks. The checkout initially lacked
locked npm dependencies; installing root, game and QA render dependencies fixed
the policy-check environment.

Pages published the slice. OCI standby initialization missed the first five-second
readiness window; retrying the same deployment succeeded without interrupting the
old producer. The three curl integration probes passed in 0.082 seconds.

Live OCI `t0_3_0` had an old full manifest (built 22:01:24 UTC). Its request returned
202 queued, then 202 building, 200 first at 14.98 seconds and 200 full at 49.24 seconds.
Every response omitted stale output. The new full manifest was built at 22:10:20 UTC.
Three further requests returned the cached full manifest without changing its mtime.
Under concurrent player/build load those requests took 491, 1837 and 3437 ms; this
proves cache reuse, not a low-latency bound. The test lease was released afterwards.

The frozen original post-deploy run passed 59/60 tests in 47.235 seconds. The one
failure was an existing synthetic paving fixture inconsistent with the newly
explicit ground materials. `ground-types` independently fixed it in `75e327b8`.
This report is committed after rebasing that fix; the selective post-deploy run is
repeated from the new frozen source via `finish --fix-for`.
