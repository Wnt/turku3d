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

## Prevention and workflow correction

The 2026-10-28 workflow was not followed in spirit: smoke checked liveness instead of health; broad synthetic asynchronous suites did not exercise changed generator behavior on OCI; the 122-second painter audit was a slowdown signal that was ignored; and the orchestrator verified only first passes before claiming recovery.

The initial remediation added a mandatory pre-switch canary. Jonni subsequently clarified that meaningful test selection belongs to the agent, and directed its removal. Deployment remains package/upload only, without a fixed build gate, queue or rollback. Client-only publication skips OCI source work using content hashes.

`/coarse/health` exposes 15-minute first/full built, failed and cancelled counts, successful-build p50/p90 seconds, success rate and last error kind. Records survive slot switches. Error kinds omit exception messages and filesystem paths. The five-second curl smoke parses this response: recent full failures/cancellations and excessive p50 are red even with HTTP 200. `codex-task checks` prints the signal and alerts. The sentinel flags full success below 80% or p50 above 30 seconds while players are online, and repeated failure kinds regardless of presence.

The agent chooses one selective asynchronous investigation within ten minutes, aware of recent runs. It must exercise the affected deployed feature. Generator changes require representative first/full real-tile builds on OCI with outcome, timing and source-count checks; client changes exercise their feature. There is no fixed tile list or generic suite substitute. The optional deployed-host probe takes agent-selected tiles and budgets, reuses stage/input caches, compares manifest building counts with gathered source buildings and collision counts with the configured tile subdivision, and records pass outcomes in rolling telemetry. It does not switch traffic.

## Measurements

Synthetic affected checks: 20 tests passed in 0.77 seconds, including a real child-worker protocol test for first/full outputs, injected failure and timeout. Deployment boundary tests prove a failed canary cannot switch the proxy, and unchanged generator bytes skip standby startup and canary entirely. The pre-merge budget remains 30 seconds. The initial build gate was subsequently removed as described above.

First live deployment attempt (`259a68114db4`): pre-merge checks passed in 2.005 seconds; Pages package/upload took 8.344 seconds. The OCI canary rejected the first pass at its 40.000-second deadline, before the proxy/source switch. Active OCI source remained `960b0c7a`. This is an observed rejection, not a successful recovery or full-pass measurement.

Incremental timing evidence in `.deploy-canary/stage-times.json` showed inputs at 0.970 seconds, all geometry stages cached (each under 0.102 seconds), and all 16 collision stages cached (each under 0.002 seconds). The remaining stall was after structural collision, in first-pass visual production. The performance owner received this evidence. Full pass was never reached, and the ≤20-second added-time target was not met. The cap was not relaxed to admit the slow source.

The initial mandatory policy retries exposed missing isolated Node dependencies (root TypeScript, game Playwright and QA Playwright). Installing each sandbox-local lockfile dependency set resolved them; no assertions were changed.

After the rejected attempt, all three integration curl probes passed in 0.405 seconds, and 22 selective scheduler/retention/public-boundary tests passed asynchronously in 0.986 seconds. These synthetic results are explicitly not evidence of successful real full-pass generation.

## Deployed health and selected real-build evidence

The corrected ungated workflow deployed `fd2f4e29acba` to OCI slot 7765 in 20.011 seconds, with no build gate. The health-aware five-second smoke passed in 0.261 seconds. This deployment included the pending facade component publication. Build-health evaluation adds no extra network request: it parses the health JSON fetched by curl within the shared five-second deadline.

The selected asynchronous investigation used frozen source `fd2f4e29acba` while subsequent source publication continued independently. It built real spawn `t0_0_0` on OCI using shared stage/input caches and separate output pointers. First pass took **9.382 seconds**; full pass took **178.293 seconds**. Both manifests contained **46 buildings**, matching the independently gathered source inputs, and **16 collision tiles**, matching the configured subdivision. Selected budgets were 120 and 180 seconds to observe completion during the known incident, rather than make package/upload wait for a build. Total async wall time including admission was **191.323 seconds**, within ten minutes.

This successful spawn build is not a world-recovery claim. The rolling health signal subsequently showed four full builds, eight failures and two cancellations, p50 30.758 seconds and p90 178.293 seconds. It correctly flagged poor full-pass success, excessive p50 and repeated error kinds. Live failures on `t0_-2_1` and `t0_-2_3` reported `ValueError: mesh extras require layer, object_id and material_key`; the facade owner was notified and identified a component missing `object_id`. The performance owner separately deployed `acaf1405`; its later timing evidence must be evaluated separately from this frozen-source run.

A subsequent concurrency review found the optional probe's fixed output directory could collide across independent frozen jobs. Probe invocations now allocate unique output directories, returned in their result, while retaining shared content-addressed stage/input caches. Their manifests, staging files and packager outputs cannot overwrite another job's evidence.
