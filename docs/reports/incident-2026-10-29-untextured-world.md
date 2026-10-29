# Untextured world incident, 2026-10-29

Times below are Europe/Helsinki. The user found the untextured world twice; successful HTTP readiness and synthetic tests had been mistaken for generator recovery.

## Timeline and impact

- 16:48: `no-coplanar-walls` (`44a2e7c6`) deployed a cross-tile wall assertion that raised on real tiles. First and full passes failed for many tiles.
- 16:56: `facade-rules-from-readings` (`ac646ec0`) deployed learned facade layouts. Its painter audit took 122 seconds; OCI full passes grew from about 10 seconds to 46–143 seconds.
- Both deployments passed selected quick checks, the five-second HTTP smoke and selective asynchronous tests (106 tests; 699 walls audited). None built a real tile on OCI.
- Moving players repeatedly cancelled full passes (`cancelled (upgrade)`). The 15-minute retention window then removed last-good full products, including textured spawn.
- 17:40: `ec6cb77b` made the neighbour check report-only. First-pass builds were observed and recovery was reported, without checking a full pass. The user still encountered the regression.

## Causes and ownership

HTTP readiness proved that the process answered, not that changed generator code could produce a textured tile. Failures and latency were only visible in service logs. A validation assertion could stop serving. Preemption plus retention converted slow enrichment into loss of all textures.

`wall-invariant-exact` owns wall validation. `full-pass-starved` owns preemption, retention and full-pass speed. This slice adds a deployment canary and live health visibility without changing either policy.

## Prevention

OCI source deployment compares content hashes of pipeline, texture and producer-service sources. Client-only publication bypasses OCI validation. When those contents change, the frozen standby source builds spawn `t0_0_0`, first then full, using the same parent stage/input caches and a separate output directory. It runs beside standby HTTP smoke. Neither source nor proxy pointers move until both succeed. Failure stops only the new standby; the old slot continues serving. The full-pass deadline is twice the median of the last 15 successful canaries, capped at 40 seconds; the bootstrap budget is 40 seconds. First pass is capped at 40 seconds. No queue or rollback is added.

`/coarse/health` exposes 15-minute first/full built, failed and cancelled counts, successful-build p50/p90 seconds, success rate and last error kind. Records survive slot switches. Public error kinds omit exception messages and filesystem paths. `codex-task checks` prints the signal and alerts; the sentinel hook flags full success below 80% or p50 above 30 seconds while players are online, and repeated failure kinds regardless of presence.

## Measurements

Synthetic affected checks: 20 tests passed in 0.77 seconds, including a real child-worker protocol test for first/full outputs, injected failure and timeout. Deployment boundary tests prove a failed canary cannot switch the proxy, and unchanged generator bytes skip standby startup and canary entirely. The pre-merge budget remains 30 seconds; the canary is part of OCI deployment.

Live timings will be appended from `codex-task finish` deployment evidence. Target added validation time is at most 20 seconds; the hard full-pass rejection cap remains 40 seconds.
