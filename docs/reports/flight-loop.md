# Flight arrival optimization loop

## 2026-10-30, iteration 1: south from the default start

Deployed baseline `998f38a7`, headed Chrome 156, Pages `?dev=1`, fresh profile,
1280×800 viewport, WebGPU. Real fly inputs climb to 70 m; 100 km/h until
100 m before the startup boundary, then 50 km/h across `t0_0_2` and `t0_0_3`.
Turn back after the full pass arrives. Built outputs for the three southern
neighbors and second target were moved aside; shared stage/input caches remained.
`before-01` only diagnosed the multiplayer-only start menu and did not fly.
`before-02` completed in 113.6 seconds including startup; its target outputs
were still absent from the first reset. Evidence is local under
`$TURKU3D_WORK/reports/flight-loop/before-02/`; screenshots stay out of git.

| Measurement | Baseline |
| --- | ---: |
| First target full visual + validated collision, after flight install | 14.27 s |
| Second target first / full visual + validated collision | 37.54 / 62.87 s |
| Second target first-pass server build | 27.33 s |
| Its walkable input preparation / stairs geometry | 22.61 / 2.59 s |
| Return to spawn after flight install | 86.91 s |
| Frame interval median | 8.34 ms |
| Main-thread long tasks, including startup | 8 |
| Return-leg asset responses / median response-header latency | 299 / 17.56 ms |

The first target's first-pass client arrival was missed because the producer's
cached collision-bundle descriptor referenced a removed file (three bundle 404s).
The subsequent full pass loaded. Return responses included 262 service-worker
synthesized responses, 12 HTTP-cache responses and 25 network responses. This
includes newly exposed surroundings; it is not a claim that every return asset
was local. Per-asset sources, durations, timestamps and bytes are in `network.jsonl`.
`work` records load, preparation, collision validation and worker registration.
OCI snapshots contain process CPU counters/RSS/swap/I/O, memory pressure/zram,
scheduler demand/build decisions, service outcomes and changing stage reports.
Public forwarder, direct Caddy/producer and multiplayer health timings are aligned
by epoch timestamps; multiplayer logs and Chrome worker trace are retained.

All 26 screenshots were inspected as contact sheets, plus the first-pass city view
at full resolution. Frames 7–9 show the missing next tile as an empty horizon and
an edge warning while the flight waits. Frames 10–15 show the plain first-pass
city; frame 16 shows the substantial texture/vegetation upgrade. The return
reveals temporarily missing distant surroundings in frame 20, followed by the
city in frame 21. Frames 22–25 are the intentional stationary return observation,
not a render stall. These visible gaps and upgrade pops remain optimization targets.

### Change and direct before/after evidence

Uncached riverside fitting unnecessarily tested and voxel-sorted all lidar returns
in the tile envelope. It now keeps the measurement corridors plus a full voxel
margin, prepares the exclusion polygon, and sorts scalar voxel indices with the
same lexicographic order. A direct OCI profile, bypassing prepared dataset caches,
fell from **20.54 s to 2.13 s (89.6%)** on `t0_0_3`. Both runs produced two patches
with identical content hash `a5241fa43df4c3d838bff4f6ed7bd1d5e8431125370a81f696ec38eff4495111`.
A prior profile attributed 11.41 s to polygon exclusion and 7.33 s to unique rows.
This comparison reuses the same shared raw-lidar read cache, not a prepared tile.

The serving cache now checks that a remembered collision bundle still exists and
recreates a removed derivative from its immutable cells. Stored facades and source
intermediates are preserved. No visual feature is removed by this slice.

The slice landed as `907a52b4`: selected checks passed in 10.2 s; Pages packaging
and upload took 7.69 s, OCI publication 11.13 s. Multiplayer remained running.
The 0.44 s smoke reached all endpoints, but rolling health was red from three
full-pass cancellations and a 35.06 s full-pass median.

Continue rotating routes and inspect every round's screenshots. Jonni's later instructions remove
the LAN cache/freeze restrictions and allow generation redesign; raw inputs and
stored characterization remain protected.

### Reproduce

```sh
source ../sandbox.env
node tools/perf/flight-loop/run.mjs UNIQUE_LABEL south  # or west/north/east
ssh -i ~/.ssh/oci_cuttlefish -o IdentitiesOnly=yes ubuntu@82.70.50.61 \
  '~/turku3d-drafts/run.sh python - t0_0_3 walkable' \
  < tools/perf/flight-loop/profile-input.py
```

The runner owns its headed profile and CDP port, never disables browser caching,
and saves screenshots every four seconds. Route crossing now also waits for
worker-acknowledged collision under the upcoming crossing point. A ten-minute
watchdog bounds an individual recording, not the ongoing optimization task.

## 2026-10-30, iteration 2: west from the default start

Deployed `907a52b4`; fresh profile and shipped-output reset for the west route,
with shared stage/input caches retained. The automated run passed in 116.22 s,
including startup and evidence collection. Evidence is in the task's frozen
`checks/907a52b42c3bf3dfdad8a85028ac200c2bdcb73f/work/reports/flight-loop/after-01/`.
This different route is coverage, not a like-for-like speedup against the south.

| Measurement | West |
| --- | ---: |
| `t0_-2_1` first / full visible + validated collision, after flight install | 22.41 / 34.65 s |
| `t0_-3_1` first / full visible + validated collision | 25.95 / 44.36 s |
| First-pass server builds, first / second target | 9.24 / 4.92 s |
| Full-pass server builds, first / second target | 8.83 / 7.87 s |
| Turn / return to spawn | 49.64 / 80.37 s |
| Frame interval median / p90 | 8.44 / 17.21 ms |
| Main-thread long tasks, including startup | 16 |
| Return asset responses / median response-header latency | 394 / 17.14 ms |

Return assets included 364 explicitly marked service-worker storage hits, 18 HTTP
cache responses and 12 network responses. Public coarse-health median/p90 were
112.32/247.72 ms; multiplayer 19.38/23.55 ms. All 24 screenshots were inspected:
frames 4–5 expose an empty horizon; frames 6–9 show plain first-pass slabs and
edge warnings, with textured neighbours appearing alongside them. Frames 10–14
mostly show full detail; frame 12 exposes the unbuilt area beyond the turn.
Frames 13–20 repopulate the return city; 21–23 are the deliberate stationary
observation. The empty frontier and visible first-to-full transition remain.

### Next measured costs and fixes

South remains the slower measured second-target route: full arrival at 62.87 s
versus west's 44.36 s. In the south trace, its first pass finished at 36.19 s,
but full work started only at 51.39 s: **15.20 s admission delay** while three
farther full upgrades occupied upgrade capacity. The next scheduler slice keeps
an extra slot available when a candidate ranks behind an unfinished urgent first
pass, allowing that nearer first pass to upgrade when ready. Committed jobs and
the existing first-versus-full ordering remain intact.

Response headers are not complete asset delivery. Reanalysis of the south return
found three home GLBs taking 1.46–1.78 s through browser storage before completion;
their total model-load wall times were 2.57–2.98 s. Model preparation then took
1.84–3.87 s. A 128 MiB immutable visual-GLB byte cache removes repeat browser-cache
delivery while preserving parsing and GPU ownership. The 18 unique visual GLBs
in that loop totalled 89.80 MiB. Memory retained by this cache is bounded;
in-flight buffers and decoder copies are additional transient memory.

CDP service-worker events sometimes report completion timestamps before the
response-header timestamp, so those small deltas cannot be treated as coherent
end-to-end durations. The next recording retains every Resource Timing entry
instead of the browser's default 250-entry buffer, records monotonic CDP event
timestamps, and reports model-load wall times separately. The runner now rejects
reused profile/evidence labels. No tests were added.
