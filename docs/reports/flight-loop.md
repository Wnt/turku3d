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
| Return-leg asset responses / median response latency | 299 / 17.56 ms |

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

Next: deploy and fly west through `t0_-2_1` / `t0_-3_1`. Route totals will be reported
separately; they cannot be used as a like-for-like speedup. Continue rotating
routes and inspect every round's screenshots. Jonni's later instructions remove
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
