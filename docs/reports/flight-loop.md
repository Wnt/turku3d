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

## 2026-10-30, iteration 3: north, interrupted for capture correction

Slice `c2ae08b2` deployed after selected checks passed in 1.22 s. Pages took
8.06 s including packaging; OCI took 9.42 s. The 1.29 s smoke reached all
endpoints, but full-pass rolling p50 was 23.48 s. Multiplayer remained running.
Jonni began concurrent live playtesting: this round includes his additional
load and demand; it is not a controlled single-player comparison.

The north flight (`after-02`) reached `t0_0_-1` first visuals + validated collision
at **158.21 s**, and `t0_0_-2` at **162.76 s**. North is now the worst observed
first-arrival direction. A retiring `907a` full build on `t0_0_-1` took **329.04 s**;
the new first build's wall time was **149.12 s**, including lock wait. Its second
target first build took 3.09 s. There were no HTTP errors in the partial capture.

Live `/proc/locks` proved the chain: old full worker 1372055 owned the global
wall-admission lock and `t0_0_-1` retention lock; old full workers 1372056 and
1372139 and new first worker 1373514 waited on global admission. New first workers
1373515 and 1373516 waited on same-tile retention. The new `t0_-1_-1` had completed
inputs in 0.787 s and cached basic walls in 0.111 s before waiting. Memory remained
healthy (17.7 GB available); CPU pressure avg10 was 0.14. This was cross-build lock
blocking, not an exhausted host.

All 47 saved screenshots were inspected. Frames 3–36 show an almost completely
empty frontier and an edge warning while waiting. Side geometry first appears
at frame 37; target geometry fills in at 39. Frames 40–43 are mostly plain first
passes; 44–45 and the final capture show textured surrounding buildings. This is
a visible streaming failure despite smooth animation during the wait.

Jonni reported canvas resizing during screenshots. The recording was deliberately
stopped at 186.53 s, preserving partial evidence; it has **no completed full/return
result** and the async job is marked failed. Earlier frame-time figures are retained
as raw observations but are not clean performance comparisons. The corrected runner
uses a real headed window (`viewport: null`, `--window-size=1280,900`) and raw CDP
`Page.captureScreenshot` without clip/scale. It checks window, canvas and resize-event
counts before/after each capture. Cold reset now acquires every route tile's retention
lock, so retiring producers cannot silently race the reset.

The next slice reports wall admission wait separately from neighbour processing.
First-pass records/admission move to an isolated namespace, while full builds keep
the legacy lock so retiring full workers remain coordinated. Boundary extraction
filters triangles with NumPy before creating Python face lists. Exact old/new JSON
matched on two cached real building meshes: 65.32→7.21 ms (23,124 triangles) and
35.67→3.76 ms (11,949 triangles). These small fixtures establish equivalence, not
a claim about total full-build speed.

Read-only profiling of the actual OCI full tile found 740,622 triangles, 83,180
boundary faces and seven matching neighbours with 145,140 faces. Under cProfile,
reading/parsing neighbour JSON took 0.65 s, recursive neighbour hashing 8.65 s,
old boundary extraction 8.16 s and overlap detection 11.29 s. Only four violations
against one neighbour triggered reassembly of the entire tile. Further work targets
that unnecessary breadth while preserving generated facade surfaces.

## 2026-10-30, iteration 4: east, capture assertion corrected

Slice `bef3a911` deployed after selected checks passed in 1.16 s. Pages took
6.96 s including packaging; OCI took 11.15 s. Multiplayer stayed running and
reported three players during deployment. Rolling health remained red; a separate
`col-budget` task owns a pre-existing 1,137,704-byte collision cell overflow on
`t0_-1_-2`/`c_-3_-8`, outside this flight's reset. Our investigation identified
exact duplicate float32 rail vertices; physical rails must remain.

The east capture (`after-03`) observed first/full models with validated collision
for `t0_2_1` by 16.50/28.76 s and `t0_3_1` by 24.68/36.91 s. These are upper bounds
from four-second progress snapshots, relative to the first snapshot, because the
capture assertion stopped the run before final in-page telemetry was saved. The
flight had turned back, but there is **no completed return measurement**.

All 14 screenshots were inspected: 3 exposes the empty frontier; 4–6 show first
terrain and plain buildings; 7–9 show vegetation and full-detail upgrades; 10–11
show populated streets; 12–13 expose missing surroundings beyond the turn.
The window stayed **1280×813 CSS pixels with zero resize events** throughout.
At shot 13, normal adaptive resolution raised the internal buffer from
1325×841 to 1445×918 while CSS size remained fixed. This was a false capture
assertion, not a device-metrics resize. The recorder now holds the resolution
controller only during capture, restores it immediately, retains capture intervals
for frame-stat exclusion, and saves partial telemetry on errors. Full-arrival
observation may now continue for five minutes before an explicit timeout event.

### Next performance slice: incremental neighbour cuts

Actual dense OCI meshes (740,622 triangles, 420 meshes) showed four external
wall overlaps triggering a repeated assembly of the whole tile. Neighbour
admission now indexes only protected neighbour faces; normal within-tile assembly
is unchanged. With matched cProfile instrumentation, the same call improved from
**over 58 s unfinished to 7.10 s complete** (more than 8.16×); the unprofiled new
call took 5.81 s. External overlaps fell from four to zero, matching legacy output.
Internal and changed/unchanged cross contacts remained zero; **417 original mesh
objects were retained** and three changed. UVs and facade programs stay attached.

This is deliberately not a byte-identical comparison with the old repeated
float32 recuts: it retains 0.1294 m² (0.0000372% of wall area) that the legacy
neighbour pass removed incidentally inside the already-assembled tile. Existing
wall, facade-overlap and reviewed paint/arch checks passed (25 cases). No tests
were added. Next run repeats the worst north route with corrected capture and
records full arrival, cache reuse and visual evidence.

## 2026-10-30, iteration 5: north completed; cold assembly and return working set

Deployed `fa89bb2b`, selected checks 3.42 s; completed `after-04` in 245.96 s
including startup/evidence. There were 52 screenshots, all inspected: first-pass
geometry is present by frames 3–5, full details appear progressively through 10–11,
and 12–41 are the deliberate wait for both target full passes. Return frames 42–46
show the city, but 47 exposes a temporary blank region near home; 48–51 repopulate
it. Every capture preserved window/canvas dimensions; zero window resize events.
Adaptive resolution continued normally between captures.

| Measurement, after flight install | North repeat |
| --- | ---: |
| `t0_0_-1` first visual + validated collision | 10.15 s |
| `t0_0_-2` first / full visual + validated collision | 20.77 / 86.59 s |
| `t0_0_-1` retained older full visual + paired collision | 171.97 s |
| Turn / return / observation complete | 171.51 / 196.87 / 208.88 s |
| Frame median / p90, excluding capture intervals | 8.35 / 16.93 ms |
| Return resource-duration median / p90 | 3.82 / 54.36 ms |
| Return GLB resource-duration median / maximum | 1,232.12 / 1,716.56 ms |
| Observed peak main-thread heap | 3,179.47 MiB |

The prior north first arrivals were 158.21/162.76 s. This improvement removes the
visible first-boundary stall, but it is not an isolated A/B: retired work, CPU
contention and generator versions differ. `col-budget` published its exact collision
compaction during this recording; observed source/instance changes were around
+109.7/+182.7 s. `t0_0_-1`'s +171.97 s full arrival was explicitly stale;
current-generation full finished at +218.78 s, after flight observation ended.
Most multiplayer samples reported two players. CPU was saturated during the first
108 s (CPU-pressure median 57%); memory and I/O pressure stayed low.

Cold local `wall_meshes` now dominates: `0_-1` spent 117.33 s there out of
153.96 s service, with 15.10 s neighbour processing and 0.00047 s admission wait.
`0_-2` spent 43.78 s in local walls out of 55.42 s. Later generations reused the
same local geometry keys in 0.33/0.72 s; warm `0_-1` still spent 16.89 s processing
neighbours. Replacement first work also waited about 34 s behind its retiring
same-tile full build; tile retention remains necessary for atomic publication.

Return's 393 asset responses were 241 service-worker storage, 139 HTTP cache and
13 network. All nine returning GLBs repeated exact earlier hashes: this is a raw
cache capacity miss, not changed content. The measured unique set was 191.23 MiB
(159.41 MiB full, 31.82 MiB first), exceeding the 128 MiB limit. Full-only retention
would still miss seven of nine return loads. The next measured cap is 256 MiB;
model ownership is unchanged. Post-flight forced GC is recorded separately from
flight timings to distinguish retained memory from temporary allocation peaks.

The largest full north model also expands to 225 MiB of facade-program attributes.
Static batching currently clones, deinterleaves and repacks them; a guarded direct
packed-buffer merge is being measured. No rendered feature or field is omitted.

The 48 HTTP failures were 36 fallback 404s on the pre-existing failing `t0_-1_-2`
and 12 ahead-request 429s under queue pressure; there were no failed GLB/texture
requests or collision-bundle errors. The collision owner has landed exact vertex
compaction, preserving rails and material seams. The loop continues with cold local
wall filtering and packed-program batching, then another south/return comparison.

The north reset waited 10 s for active tile locks before moving outputs aside;
this preparation delay is outside the flight-install timings above. The subsequent
first-arrival comparison therefore includes a corrected reset protocol as well as
performance changes.

### Next slice, measured independently

Split neighbour overlap indexing now excludes faces that the existing split filter
would always discard: matched cProfile **12.16→4.34 s**, identical qualifying pairs
and exact area values for seven real neighbour snapshots, with 23.65 MiB lower peak
RSS. Cold within-tile assembly applies order, height and 3D bounds before gathering
normal vectors: **19.19→13.80 s** on 256 real dense-facade chunks, with identical
projected pair order and area bytes. Three complete real facade cuts also matched
exact output hashes (30,238 input / 30,242 output triangles).

Client batching directly concatenates compatible packed facade-program buffers,
transforming/merging standard attributes through the existing path. Unsupported
layouts retain the general merger. A cached real north model matched every final
attribute/index byte, matrix, bound and semantic range across 46 output meshes;
release restored original geometries unchanged. The CPU probe improved batching
**635→213 ms**, and process peak RSS **1,278→1,008 MiB**. Texture loading was stubbed
in this CPU probe; browser/GPU behavior is the next deployed observation. The byte
cache cap and post-flight GC measurements described above are part of this slice.

Other agents' collision compaction and reflection/resource-retirement changes are
rebased before the next run and must be included when interpreting client timings.
