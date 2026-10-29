# Draft generation performance on OCI, 29 October 2026

The paired basic visual/collision path is deployed. The ten-player cold capacity target
is **not yet met**: the production-cache paired 120 km/h run missed one of 182
crossings by 1.405 seconds. An isolated run with empty generated caches missed 147
crossings, including 75 unavailable at measurement end. Office-start driving at
50 km/h passed. These are real HTTP service
measurements, not browser GPU or physics-frame measurements.

## Contract and changes

A first pass creates terrain, roads, building shells, bridges and tunnels without
texture encoding, detailed facades or decorative objects. It publishes a flat-colour
GLB made from the collision triangles, all sixteen collision payloads and the road
graph through one atomic metadata pointer. The client loads and prepares graphics
before exposing collision. Full upgrades also swap the pair together; failures keep
the previous usable pair. Existing facade descriptions and generated assets survive.

The generator now batches surface intersections in GEOS/NumPy, caches indexed
GeoParquet reads and area inputs, selects building rows before classification, skips
decorative input preparation in first passes, and batches triangle/bridge assembly.
Per-stage input identities and a separate structural producer identity avoid rebuilding
structural geometry after unrelated facade changes. Urgent structural demand can
replace less urgent queued work. First passes take queue priority and can cancel running full upgrades when
urgent structural demand needs their slots. Four CPUs still share running CPU work.

The latest tunnel change batches terrain-cut classification and wall triangle appends.
On t0_6_-1, tunnel time fell from 8.703 to 7.537 seconds. Batched terrain
intersections and culling wall sections outside the tile then reduced it to 5.791 seconds. Cold input preparation differed
between runs, so total wall time is not a controlled comparison. Canonical collision
triangles, winding and materials match bitwise on t0_6_-1 and t0_1_2: 32 cells,
112,822 triangles. The 17 tunnel regression tests pass.

Stair treads, risers and side faces now use native array batches and shared corner
samples; flights safely outside the tile skip terrain preparation. Separate cProfile
measurements of `stairs.produce` fall from 1.937 to 0.712 seconds on t0_2_0 and from
0.601 to 0.016 seconds on t0_-3_1. Canonical collision triangles remain bitwise equal
on both tiles: 32 cells and 94,379 triangles. Manifest timings now expose river-bank,
stair geometry, stair ground cuts and kerb geometry separately from input preparation.

## Measurement method

Hardware: OCI linux-aarch64, four Neoverse-N1 cores, 23 GB RAM. Real city inputs are
read-only. Diagnostic outputs and caches live under the agent-owned
`/srv/turku3d/work/generator-speed/reports/`. Cold means empty generated tile caches,
not flushed OS caches. Warm means a repeated build in the same process. Later tiles
benefit from shared process/area caches. Shared texture assets are reused once in an
owned pool. Timed runs below have cProfile disabled; separate profiles identify hot
functions. No paid services or new keys were used.

The controlled stage table compares t0_1_2 before the optimization series
(producer `50fa7ec21fe4da58`) with the input/geometry batching slice
(`585869790b3f61b4`). This historical first pass still contained extra visual work;
the currently deployed basic-pair path is measured separately below. Values are
manifest elapsed seconds, rounded to milliseconds. Zero means skipped/not applicable.
Rows overlap: ground includes bridges, and visual_geometry includes surface_boundaries.
The baseline fences warm timer replayed cached generation time and is not a valid
warm speedup; nested package timers had the same issue, so only the outer package
timer is shown. Input acquisition and overhead explain the remainder of total time.

### Tile caches cold

| Stage | First before | First after | Full before | Full after |
|---|---:|---:|---:|---:|
| ground | 0.095 | 0.090 | 0.355 | 0.287 |
| wfs | 2.318 | 1.017 | 4.934 | 2.321 |
| roads | 1.138 | 0.809 | 1.365 | 0.445 |
| parking | 2.028 | 0.000 | 2.111 | 0.185 |
| poles | 0.000 | 0.000 | 1.515 | 0.122 |
| tunnels | 2.387 | 2.175 | 2.129 | 2.004 |
| stairs | 0.187 | 0.046 | 0.186 | 0.044 |
| ortho | 0.000 | 0.000 | 0.504 | 0.445 |
| trees | 0.000 | 0.000 | 0.236 | 0.028 |
| forests | 0.000 | 0.000 | 0.340 | 0.033 |
| barriers | 0.000 | 0.000 | 0.300 | 0.048 |
| surfaces | 0.000 | 0.000 | 0.383 | 0.075 |
| markings | 0.000 | 0.000 | 0.199 | 0.052 |
| kerbs | 0.000 | 0.000 | 0.201 | 0.027 |
| appearance | 0.000 | 0.000 | 0.306 | 0.080 |
| register | 0.000 | 0.000 | 0.117 | 0.109 |
| buildings | 0.502 | 0.478 | 0.323 | 0.211 |
| surface_boundaries | 0.000 | 0.000 | 7.481 | 2.166 |
| vegetation_scatter | 0.000 | 0.000 | 0.137 | 0.132 |
| fences | 0.005 | 0.008 | 0.109 | 0.119 |
| visual_geometry | 0.763 | 0.170 | 9.245 | 2.955 |
| surface_library | 0.000 | 0.000 | 0.002 | 0.002 |
| package | 0.570 | 0.500 | 2.463 | 1.303 |
| collision | 0.351 | 0.257 | 0.321 | 0.261 |
| parked | 0.850 | 0.001 | 0.774 | 0.781 |
| total | 13.373 | 7.785 | 29.821 | 13.188 |

### Tile caches warm

| Stage | First before | First after | Full before | Full after |
|---|---:|---:|---:|---:|
| ground | 0.007 | 0.007 | 0.014 | 0.014 |
| wfs | 0.004 | 0.003 | 0.006 | 0.003 |
| roads | 0.001 | 0.001 | 0.001 | 0.001 |
| parking | 0.013 | 0.000 | 0.013 | 0.013 |
| poles | 0.000 | 0.000 | 0.001 | 0.000 |
| tunnels | 0.028 | 0.113 | 0.034 | 0.032 |
| stairs | 0.001 | 0.000 | 0.001 | 0.000 |
| ortho | 0.000 | 0.000 | 0.007 | 0.006 |
| trees | 0.000 | 0.000 | 0.001 | 0.000 |
| forests | 0.000 | 0.000 | 0.001 | 0.000 |
| barriers | 0.000 | 0.000 | 0.001 | 0.000 |
| surfaces | 0.000 | 0.000 | 0.002 | 0.002 |
| markings | 0.000 | 0.000 | 0.002 | 0.001 |
| kerbs | 0.000 | 0.000 | 0.001 | 0.000 |
| appearance | 0.000 | 0.000 | 0.002 | 0.001 |
| register | 0.000 | 0.000 | 0.001 | 0.001 |
| buildings | 0.024 | 0.022 | 0.030 | 0.028 |
| surface_boundaries | 0.000 | 0.000 | 0.016 | 0.016 |
| vegetation_scatter | 0.000 | 0.000 | 0.035 | 0.033 |
| fences | 0.005 | 0.002 | 0.109 | 0.004 |
| visual_geometry | 0.149 | 0.040 | 0.459 | 0.106 |
| surface_library | 0.000 | 0.000 | 0.002 | 0.002 |
| package | 0.023 | 0.023 | 0.048 | 0.048 |
| collision | 0.187 | 0.153 | 0.220 | 0.188 |
| parked | 0.084 | 0.001 | 0.093 | 0.102 |
| total | 0.613 | 0.562 | 1.041 | 1.000 |

## Current paired first pass

Producer `7a39d2e756ad66d4`, commit `441997fad627`, before tunnel batching.
The same Sources instance visited the office, t0_1_2 and then the hot bridge/tunnel tile.
Total is wall-clock time; basic GLB is the publication-helper time, including hashing.

| Tile | Cold total | Warm total | Cold basic GLB | Warm basic GLB |
|---|---:|---:|---:|---:|
| t0_0_0 | 7.836 | 0.521 | 0.047 | 0.047 |
| t0_1_2 | 2.821 | 0.384 | 0.040 | 0.040 |
| t0_6_-1 | 14.953 | 0.790 | 0.079 | 0.077 |

Collision-ready to paired-ready adds 40–143 ms including tunnel road-graph height
publication. The GLB helper itself adds 40–79 ms. The hot tile spends 3.464 seconds
in bridges and 8.703 seconds in tunnels; these remain material cold costs.

## Live driving probes

`tools/remote-drafts/ten-drivers.py` follows directed city road graphs. Ten drivers start
in distinct radial directions, drive at constant 120 km/h, send thirty-second route
hints and visible/predicted tile demand, and poll with browser-like retry intervals.
Availability requires SHA-checked visual and all sixteen collision payload downloads.
Routes may turn abruptly, so this stresses delivery rather than simulating vehicle
acceleration. Start-up waits for all initial tiles, then crossing deadlines start.
CPU is aggregate host use (400% maximum), including other live work. Queue is peak
service queue length. The live tile/area caches are reused: this is not an empty-world
capacity test, and repeated route runs must not be labelled cold.

| Probe | Startup s | Crossings | Late | Missing | Worst late s | Queue peak | CPU % |
|---|---:|---:|---:|---:|---:|---:|---:|
| Office, 1 driver, 50 km/h, 120 s | 1.006 | 12 | 0 | 0 | 0 | 7 | 299.1 |
| 10 drivers, 120 km/h, 120 s | 10.542 | 182 | 1 | 0 | 1.405 | 128 | 354.8 |

Both used stable producer `7a39d2e756ad66d4`. The missed crossing was driver 4 entering
`t0_-3_1`: arrival 10.669 s, pair ready 12.074 s. Earlier collision-only tests reached
zero lateness on a warm repeat, but that contract permitted invisible obstacles and
is not acceptance evidence for the paired implementation.

### Isolated cold capacity

The same public handler and four persistent workers ran on an owned OCI probe port,
with empty generated tile, prepared-input and shared-area caches. Raw data was already
provisioned, read-only, and fetching disabled. Production remained live, so CPU includes
other activity. Producer `d984f2dd2f7b3ab3` includes the terrain batching above.

| Probe | Startup s | Crossings | Late or missing | Missing | Worst completed late s | Queue peak | CPU % |
|---|---:|---:|---:|---:|---:|---:|---:|
| Cold, 10 drivers, 120 km/h, 120 s | 84.085 | 182 | 147 | 75 | 25.139 | 116 | 312.2 |

There were no generator failures, but HTTP request timeouts occurred under cold
contention. Missing tiles are failures with unknown lateness; the finite worst value
must not be interpreted as their upper bound.

Profiling then found that first admission computed a full input identity for failure
backoff even when no failure existed. Ten initial requests spent 17.851 of 18.005
seconds in that identity path, including hashing 154 raw files under the scheduler
lock. Limiting that work to a recent recorded failure reduces the identical ten-request
cold admission probe to 0.184 seconds. Both measurements include cProfile; they are
request-admission measurements with no build workers, not tile-generation times.

The first post-fix cold driving rerun is invalid: the shared data volume reached
ENOSPC before the summary could be written. Its owned derived lidar caches and
intermediate stages were removed after the process stopped. Production health then
reported online with zero failed tiles. The diagnostic now requires 8 GiB initial
headroom and aborts below 2 GiB free. No post-fix driving pass is claimed.

## Reproduction

Source the sandbox environment first. Run real-input diagnostics through a host slot
with a new owned output directory and a frozen source checkout:

```sh
python scripts/dev/host_slots.py build -- python tools/perf/draft-profile.py   --root "$TURKU3D_WORK/reports/new-profile" --tiles t0_1_2 --phases first full   --assets /srv/turku3d/work/generator-speed/reports/after-source-cache/t0_1_2/full
```

Add `--profile` for separate cProfile evidence; do not compare its elapsed times with
the unprofiled stage table. The JSON report records producer codes and cold/warm rows.
For the isolated cold service, use a fresh owned root and an owned probe port:

```sh
python scripts/dev/host_slots.py build -- python tools/remote-drafts/cold-drive.py \
  --root "$TURKU3D_WORK/reports/new-cold-service" --port "$((TURKU3D_PORT_BASE + 3))"
```

Run the HTTP probe against production with `tools/remote-drafts/ten-drivers.py`; its summary
records producer changes, queue lengths, CPU, late/missing crossings and cache-visible
startup time. Probe reports are local evidence, not raw city data committed to git.

## Remaining work

Repeat isolated cold capacity after the admission fix. Continue reducing cold
input/geometry costs and early route deadline misses. A warm
pass alone does not prove ten players can explore arbitrary uncached directions.
