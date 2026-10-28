# Draft scheduling: centre drive and bounded demand

Measured 2026-10-28 on the production OCI service via the public HTTPS boundary,
with the Pages Origin. The scripted drive runs 120 seconds at 50 km/h east along
engine z=-375, starting x=-375. It crosses seven new 250 m tiles. Source before:
`7847e0e9`; source after: `d287bbf6`. No city-area build or browser run was used.

## Results

| Metric | Before | After |
| --- | ---: | ---: |
| First/edge pass ready before crossing | 7/7 | 7/7 |
| Current textured upgrade by arrival +5 s | 4/7 | 7/7 |
| Worst observed textured arrival delay | 12.15 s; two still absent at end | 3.95 s |
| Live queue maximum | unavailable | 11 |
| Simultaneous generation workers | configured 4 + separate upgrade lane | measured 4 |
| Sampled worker seconds outside current want-list | unavailable | 0 / 341.3 s |
| Durable unfinished requests maximum | 54 (46 outside wants) | expiring demand |
| Deployment startup warming scope | 92-tile ring | 6 startup cells |

The before journal count is a backlog proxy, **not** a live queue measurement.
The old public boundary did not expose the queue or worker intervals. The after
measurement samples the private scheduler snapshot once per second, so worker
seconds are wall-time estimates at that resolution, not CPU accounting.

These are cached first passes, including complete previous-generation output
served during revalidation. The full upgrade checks require the current producer.
The after drive reuses artifacts warmed by the before drive; this is a same-route
operational comparison, not a controlled cold-cache throughput benchmark. It meets
the scripted-drive target but does not establish cold raw-data performance everywhere.

## Per-tile after measurements

Times below are seconds since that tile's first request; arrival is seconds since
drive start. The initial occupied cell is included but excluded from the seven
upcoming crossings.

| Tile | Arrival | First pass latency | Full upgrade latency | Full delay after arrival |
| --- | ---: | ---: | ---: | ---: |
| `t0_-2_-2` | 0.00 | 0.34 | 13.70 | 13.70 |
| `t0_-1_-2` | 9.75 | 0.34 | 13.70 | 3.95 |
| `t0_0_-2` | 27.06 | 0.98 | 17.21 | 0.00 |
| `t0_1_-2` | 45.71 | 0.98 | 22.07 | 0.00 |
| `t0_2_-2` | 63.64 | 0.51 | 20.44 | 0.00 |
| `t0_3_-2` | 81.56 | 0.51 | 9.44 | 0.00 |
| `t0_4_-2` | 99.29 | 0.86 | 4.10 | 0.00 |
| `t0_5_-2` | 117.41 | 0.64 | 6.23 | 0.00 |

## Scheduling and cancellation

Anonymous tab leases replace the previous want-list each second and expire after
12 seconds. Only ordered ids, urgency and visibility leave the browser. Upcoming
crossings precede upgrades, visible upgrades precede remaining wanted neighbours,
and one of four slots stays available while upgrades run. Other clients retain
shared tiles. Removed tile polls with the old tab id cannot resurrect them.

A live cancellation probe requested `t0_6_0`, observed its full rebuild running,
and replaced the want-list with an empty list. The worker logged `cancelled` after
0.45 seconds of build time; the tile disappeared from the sampled running set
within 1.10 seconds of release. Its previous complete cache remained available.
Almost-done stale work finishes at reduced priority; overdue stale work cancels.

`setRouteHint(points | null)` accepts the remaining mission polyline in engine x/z
metres. Prediction follows its bends for 30 seconds of current speed; hints farther
than 80 m are ignored. The polyline remains in the browser. Default motion
prediction remains available when there is no usable route hint.

## Startup, cache reuse and failures

The warmer now enumerates office/car-search and multiplayer startup coverage only:
`t0_-1_0 t0_-1_1 t0_0_0 t0_0_1 t0_1_0 t0_1_1`. Saved player/car starts are covered
by the existing browser startup union, with bounded ordered demand. The warmer
pauses and its running work yields whenever player requests are pending.

The observed 28–34 s old upgrades included full-pass WFS preparation (4–6 s),
vector surface-boundary splitting (8–10 s), poles and final packaging. A structural
first pass does not populate all full-pass caches. Rebuilding the entire 92-cell
ring after updates multiplied that cost. Orchestration changes are excluded from
producer identities, and prepared/stage caches are retained across source switches.
The scheduling regression test checks that scheduler edits cannot invalidate raw
preparation or producer output. Production evidence after the geometry fix:

| Instance | Terrain | Bridges | Buildings | Tunnels | Surface boundaries | Package | Full build |
| --- | --- | --- | --- | --- | --- | --- | ---: |
| `t0_4_-3` | hit | hit | miss | miss | miss, 6.36 s | miss, 1.19 s | 10.27 s |
| `t0_5_-2` | hit | hit | miss | miss | hit, 0.016 s | miss, 0.82 s | 4.48 s |

Building and tunnel misses are expected for the production geometry corrections:
folded approach polygons are repaired before intersection, and carved building
shells use their retained collision owner's export precision. Both formerly
failing upgrades are now full/current (`934ad67c439a57a9`), built at
20:27:13 and 20:27:25 UTC respectively. Collision validation remains unchanged.

## Delivery and verification

`codex-task finish` merged and published Pages, then used `deploy-drafts` to switch
OCI to the standby producer at 20:25:11 UTC. Caddy and multiplayer were not
restarted. Six startup cells warmed; the service returned to idle afterward.
The final delivery additionally verifies the standby's random process instance
through the proxy before stopping the old writer, and continuously probes cached
full textures during the switch. The private task report holds that receipt.

Focused initial gate: 33 Python tests, 15 client tests, typecheck and deployment
ordering, all selected commands in 1.796 s. Post-deploy frozen-source selection:
61 passed in 32.47 s (33.27 s including admission). Commands:

```sh
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_demand.py pipeline/tests/test_coarse_latency.py pipeline/tests/test_coarse_tunnels.py tools/remote-drafts/test_public.py
npm --prefix game run test -- tests/unit/coarse-region.test.ts tests/unit/coarse-tiles.test.ts
npm --prefix game run typecheck
python3 scripts/dev/tests/run.py test_deploy_drafts.py
# On OCI, with private scheduler telemetry available:
python3 tools/remote-drafts/drive.py --demand --output ~/turku3d-drafts/reports/fix-draft-scheduling-after.json
```

Private JSON evidence remains under the task's `work/reports` and OCI's
`~/turku3d-drafts/reports/fix-draft-scheduling-{before,after,cancel}.json`.
