# Arrival warm cache

The scheduler warms current full tiles immediately and uses at most one spare online worker, retaining the existing two-minute all-worker idle expansion. Anonymous client want-lists are interleaved; recent visible tiles survive restarts for 24 hours (256 entries). Urgent demand preempts warm work; visible warm jobs become committed foreground work. Boot/data free-space floors are 5/15 GiB and recover automatically.

Kerb, tunnel and parked stages now hash the height sampler they use instead of all surface producers. No geometry, texture or object feature changed.

## Historical direct-key replay

Latest ten content-distinct uploaded OCI source transitions at audit time. These are direct code identities with unchanged raw data; upstream output changes can additionally invalidate downstream stages. After is a counterfactual replay of the narrowed sampler dependencies. The first deployment changes these three keys once.

| Source | Direct invalidations before | After | Avoided stages |
|---|---:|---:|---|
| a6293172 | 4 | 4 | — |
| 41601d0b | 11 | 11 | — |
| 1932072a | 4 | 4 | — |
| abb6a2e6 | 2 | 2 | — |
| e05b0e9b | 10 | 7 | kerbs, parked, tunnels |
| d1274164 | 0 | 0 | — |
| 279d7366 | 3 | 3 | — |
| 09256076 | 5 | 5 | — |
| 8d5cdc04 | 0 | 0 | — |
| f670b871 | 1 | 1 | — |

| Stage | Before | After |
|---|---:|---:|
| bridges | 3 | 3 |
| building_layer | 1 | 1 |
| building_visual | 3 | 3 |
| fences | 2 | 2 |
| forest_path_ground | 1 | 1 |
| input/forests | 2 | 2 |
| input/markings | 1 | 1 |
| input/parking | 1 | 1 |
| input/roads | 3 | 3 |
| input/surfaces | 1 | 1 |
| input/tunnels | 3 | 3 |
| kerbs | 2 | 1 |
| package | 2 | 2 |
| parked | 2 | 1 |
| river_banks | 1 | 1 |
| stair_ground | 1 | 1 |
| surface_boundaries | 1 | 1 |
| tunnels | 4 | 3 |
| vegetation | 2 | 2 |
| wall_meshes | 3 | 3 |
| wall_visual | 1 | 1 |

Total direct stage invalidations: 40 → 37. Kerbs 2 → 1; parked 2 → 1; tunnels 4 → 3. This does not claim all forty caused a costly real rebuild.

## Before-merge OCI online evidence

Against published source with an isolated new-scheduler overlay and retained prepared/stage/texture caches: old online controller admitted zero jobs; new controller completed both first and full passes without explicit tile requests. Maximum one warm job despite continuous online heartbeats. Spawn: first 3.892s, full 9.358s, ready 16.272s. Bridge: first 6.707s, full 124.002s. All 46/24 buildings and 16 collision cells per tile preserved; zero failures/cancellations. This demonstrates online warming, not a fresh-generation latency improvement; bridge facade/wall stages missed their seed caches.

## Shared browser yardstick

Same fixed corridor and durations in tile-arrival-profile.mjs. Before basic/full arrival counts: 50 km/h 4/7 and 0/7, unsupported 45.25s; 200 km/h 7/13 and 0/13, unsupported 28.25s; 500 km/h fast fly 2/16 and 0/16, unsupported 24.25s. Post-deploy and steady-state results are recorded in the agent-owned ten-minute async run. Concurrent source deployments/players affect these observations; no controlled attribution is inferred.

## Reproduction

```sh
source ../sandbox.env
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_idle.py pipeline/tests/test_coarse_demand.py pipeline/tests/test_coarse_sources.py
python3 scripts/dev/audit_stage_invalidations.py
python3 scripts/dev/probe_warm_arrival.py --commit "$(git rev-parse HEAD)"
```

Raw reports stay in `$TURKU3D_WORK/reports/`: stage-invalidations.json, warm-before-merge.log and tile-arrival/. The owned OCI evidence root is `/srv/turku3d/work/arrival-warm-cache-15f5f5f1ffa4/coarse`.


## Deployment and forward correction

Initial slice `3c06dd35`: selected35 cases and mandatory policies passed in14.615s; OCI activated in16.884s at19:45:55UTC. Curl smoke completed0.505s: integration endpoints up, health red (38full built,4failed,0cancelled;90.5% success,p5038.639s).

The first137.928s async run completed both real OCI warm pairs with correct46/24 buildings and16 cells per tile, zero failures/cancellations: spawn first/full7.518/24.170s; bridge7.699/71.746s. It failed before browser traversal because the public menu no longer exposes the old multiplayer selector. Concurrent deployments later changed the active source again; frozen OCI results remain tied to3c06dd35.

The one forward correction uses the measured public-menu text selector and fixes raw-wait admission: pending data uses no worker slot, its lease stays current until landing, and background raw waits remain bounded to the worker count. Prepared warm tiles continue building while those raw downloads wait. NoCoverage/unavailable accounting from LAN readiness is preserved. The linked deployed probe includes a deliberately held raw-input tile ahead of both real warm tiles.


## Renewed strict-priority correction

On renewed coordination request3263241e50204d1598ca93d02dc40b3e after Jonni reported slower terrain, online warming now yields to **any** queued actual first/full player request. Requested tiles promote existing useful warm work; remaining warm jobs are cancelled before dispatch, regardless of spare worker count. Warm first/full ordering always follows requested work. Online/just-activated warming only uses tiles whose complete raw inputs already exist; background downloads retain the original fully-idle grace. The online worker cap remains one.

Owned OCI build probes now inherit nice19, bind to one CPU, honor the remaining owner deadline and handle transport/deadline termination by cancelling their own worker tree. The previous queued forward run exhausted its ten-minute budget before any traversal completed; escaped owned remote processes were explicitly stopped. No late measurements are counted. The renewed verification uses one explicit browser reservation and no isolated production tile-build probe.

Overload observations are retained: serving agent reported0/7 basic/full at50km/h,115.75s unsupported, and ten-driver startup failed with1411 transport/parse errors at380.7% CPU across changing generator identities. These are concurrent-load observations, not a controlled result of this correction. The112a readiness census recorded spawn6/6 and mission routes25/885. Global ten-driver readiness remains unproven.
