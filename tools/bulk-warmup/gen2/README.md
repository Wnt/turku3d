# Gen 2 city staging — 2026-11-02

Host `root@185.20.139.132`, root `/root/turku3d-bulk-gen2`, Ubuntu 24.04,
300 GB MaxIOPS. **Staging complete; no full city build or resize has been run.**
Only the coordinator uses the UpCloud API, and Jonni must approve resizing.
Never access the UpCloud token, touch vm-control, change OCI services, use the old
hold/prebuild scripts, write snapshots, push, deploy or merge main.

## Staged inputs and environment

The municipality is OSM Turku relation 399906. `plan.py` unions its 5,245 L0 cells
with the old 995-cell walk: **5,812 candidates**, **7,003 dependency-halo cells** and
**2,825 far 1 km cells** within 15 km. The map is
`~/turku3d/loops/bulkgen2/tile-map.png` on the Mac. Published ground lidar covers
**5,234 candidates (90.05%)**. The remaining **578 are explicit full-build exclusions**;
regional terrain covers their scenery but does not supply full collision products.

All **6,371 acquisition jobs completed**, with zero failed or missing downloads.
The input halo was fetched before any tile build, including neighbouring spot heights.
`state/PREFETCH_COMPLETE.json` and `prefetch-coverage.json` record coverage/exclusions;
per-unit receipts make retries resumable. WFS/lidar/imagery fetch concurrently;
OSM clipping alone is serialized because concurrent osmium exceeded the 4 GB plan.
Every imagery quadrant is checked even if another quadrant has no public coverage.

Frozen source version:
`4f7320b8e0f5f06b44e50a6cac59f7716f540a07b44783fb069e696b31df8516`.
It contains **207.177 GB / 177,306 files**, hard-linked without duplicating raw data.

| Source | GB, decimal |
|---|---:|
| Classified + ground lidar | 184.704 |
| Ortho | 10.440 |
| WFS | 6.005 |
| OSM | 1.038 |
| Existing streetview cache | 0.113 |
| Textures | 3.175 |
| Facade assets, approvals, characterization | 1.522 |
| Gen 2 supplements | 0.113 |
| Regional terrain and land cover | 0.067 |

OCI raw/textures/facade files were rsynced **directly to UpCloud**, nice 19 / ionice
idle, using the explicitly authorized dedicated OCI SSH key. Only that key setup
changed OCI. Public classified lidar, WFS and imagery were fetched on UpCloud using
the pipeline fetchers. The 64 × 84 km regional arrays were rebuilt from public
Copernicus/ESA COGs, SHA `7d3428185d56647f307ae7a818617a70f25ed2e3514b3b72f11eccb48856c476`.

osgallery-dev uploaded only its Gen 2 source overlays: **435,893,459 regular-file
bytes** (439 MB rsync sender traffic), below 2 GB. No bulk lidar or horizon tree
upload. Inventory evidence: Mac `loops/bulkgen2/source-inventory.json.gz`.

Environment matches the farm: locked Python 3.12.16, Node 24.18.0, KTX 4.4.2,
meshoptimizer 1.3.0, micromamba/npm dependencies, triangle/jsonschema. `run.sh` sets
paths and one BLAS/OpenMP thread per builder. Rayon, Numba and explicit job pools
use `TURKU3D_THREADS_PER_WORKER` (default four; the farm sets CPU count / workers).
Source-order LAZ decoding is shared across surveys, workers and generations in
the farm's content-addressed raw-derived cache. All caches remain on **MaxIOPS**.

## Revision and source identity

Tested archive: `62f7c0ce0832274b292c8bf5b0d36856edc190c9`.
Producer generation: `cc86955fe5dd72b9d74715c31e04a785b1817d32527f21839926a3103b9ec31c`.
Source-bound generation: `4c1bad6b78f0b9ae0c161057d1af9b644f779880023c753e955ca783b7dc4fba`.
Bound revision: `5c9740d33b1aac7206f70791a670829eaf4a7ed5641062a93a5bca345a2ad304`.

The coordinator selects the final committed revision from the Mac:

```sh
python3 tools/bulk-warmup/gen2/pin.py REF
```

`pin.py` copies `git archive` and takes the acquisition/build/proof locks before
switching the immutable checkout. If acquisition code/config changes, rerun
`prefetch.py ROOT/state --workers 3` and `freeze.py ROOT`. Geometry/client changes
can reuse acquisition. `--adopt-initial-receipts` was a one-time completed migration;
do not use it for ordinary retries. Never copy old absolute-path stage caches.

After a changed producer, prove the same three tiles before the full run:

```sh
ROOT=/root/turku3d-bulk-gen2
"$ROOT/run.sh" python "$ROOT/ops/bulk.py" "$ROOT" --report-dir "$ROOT/state/profile" \
  --tiles t0_0_0 t0_1_0 t0_-1_0 --workers 1 --worker-memory-gib 2.9
```

Reports can be isolated with `--report-dir`; acquisition/build locks remain common.
An idle memory-admission stall now exits **75**, preserving completed tiles. The
small-plan memory reservation is calibrated to the proof; the 64-core command below
retains 3 GiB per builder. Do not fold idle admission waits into build-time estimates.

## Proofs and profiles

Three original proofs took **207.524 / 153.514 / 62.346 s** wall; combined CPU
**370.516 s**, or **2.058 CPU-minutes/tile**. Peak child RSS was **2.81 GiB**.
Instrumentation then rebuilt those tiles from the same frozen sources: **every
published payload hash remained identical**, with no size/count differences.

The three instrumented products were pulled to the owned osgallery proof farm and
validated by the exact producing revision's `shared_tiles.Farm.validate`.
**3/3 same-generation comparisons match every payload hash, size and count**, including
all 48 collision cells. Evidence on the Mac: `profile-comparison.json`,
`receiver-comparison.json`, `profile-proof.json` and `receiver-proof.json`.

The older LAN reference had the same original producer but different source selection:
WFS `work-3x3` versus prefetched coarse blocks, additional neighbouring spot heights,
and one extra classified-lidar neighbour. Its dated OSM clip was unchanged. Against
that older snapshot, 45/48 collision hashes matched; visual triangle differences
were -7/+15/-13. `compare.py` reports normalized source selections and all differences;
these were not falsely labeled identical snapshots.

WFS-block density classes contain 3,391 dense, 1,046 sparse and 797 no-building tiles.
These are buffered source-row classes, not exact building counts or a water mask.
Two extra cold-stage samples passed: sparse `t0_-1_30`, **98.077 s wall / 89.450 s CPU**;
no-building `t0_-30_1`, **8.969 s wall / 6.081 s CPU**. `classify.py` records the
classification and `state/class-seconds.json` supplies the measured ordering costs.

Input preparation dominates. Dense-tile canopy: 35.26 wall / 34.60 CPU s; walkable:
33.66 wall / 30.15 CPU s. Sparse inputs: 94.33 wall / 86.94 CPU s. Named input-lock
waits were **0.00019–0.00035 s/tile** in these one-worker samples; this does not predict
contention at 80 workers. Warm packaging was 0.19–2.79 s. Independent tile workers
parallelize the dominant input work; extra encoder threads are not justified by this
profile. Ten ground-material channels warmed successfully in 1.047 s.

Stage/input reports contain inclusive CPU and named lock waits, including live Node
children. Nested rows overlap; do not sum parent and child rows. Texture compilation
also records `lockWaitSeconds`. Fingerprints already use per-source locks, not the
legacy global hash-index lock. No download occurs inside these builds.

## 64-core run and first-ten-minute ramp

**Expected wall time: 2.5–3 hours; provision a 4-hour window until the ramp confirms it.**
Class-weighted measured CPU gives 143.67 core-hours, an ideal **134.7 minutes at
64 cores**. This is a five-tile estimate with coarse density classes, not a measured
city throughput. Scheduling, storage and cache churn must be checked during the ramp.

After Jonni's resize approval and final revision selection, on UpCloud:

```sh
ROOT=/root/turku3d-bulk-gen2
nohup "$ROOT/run.sh" python "$ROOT/ops/bulk.py" "$ROOT" \
  --walk "$ROOT/state/walk.json" --workers 80 --worker-memory-gib 3 --reserve-gib .5 \
  > "$ROOT/state/city.log" 2>&1 < /dev/null &
printf '%s\n' "$!" > "$ROOT/state/city.pid"
mpstat -P ALL 5 120 > "$ROOT/state/first-ten-minutes.txt"
```

The dedicated pool supports 96 workers; the LAN farm limit is unchanged. Admission
also honors total/available RAM and an 8 GiB memory floor. On a 256 GiB host the
3 GiB reservation caps effective workers at 82; a larger target needs measured RAM
headroom before changing the reservation. Start at **80**. Inspect `utilization.jsonl`
and mpstat, aiming for <5% idle while distinguishing memory admission, I/O and locks.
Raise/lower the live target without restarting via atomic JSON replacement:

```sh
printf '{"workers":88}\n' > "$ROOT/state/workers.next"
mv "$ROOT/state/workers.next" "$ROOT/state/workers.json"
```

Measured tile durations override measured class estimates; centre tiles break ties.
Large/slow work runs first, cheap outer work last. Before city admission, `warm.py`
requires the three verified proof products for the generation and warms all ground
channels. Serial proofs already warm compiled textures and shared vehicle assets.

## Disk control and verified pulling

Current UpCloud use: **212 GiB used / 72 GiB free**, including 9.9 GiB derived caches
and 387 MiB proof generations. Estimated city published tile payload: **53 GB**;
counting all referenced shared bytes separately for every tile gives a conservative
**115 GB** total before cross-tile deduplication. Neither output estimate includes
rebuild scratch. Do not retain the whole city alongside the 207 GB sources on this disk.

At 80 workers, the 25 GiB floor plus 0.5 GiB/worker requires **65 GiB free** before a
new wave. Completed input/stage entries are trimmed under their content-key locks.
When pressure drains all workers, raw-derived caches can be evicted and full-wave
headroom is restored. Active readers stay protected. Bulk eviction pins older product
generations too: only a **verified receiver acknowledgement** permits product pruning.
The three validated warm seed tiles remain on the host (<200 MiB) so a resumed
run can repeat its preflight. Exit 75 means restore headroom and resume the same command; completed/exported tiles
are skipped. Never lower the 25 GiB floor to force progress.

The receiver uses a **verified source catalog plus non-raw overlays**, not a second
207 GB raw clone. It retains the full content-hash inventory, code, regional data and
small Gen 2 supplements. Receipt `raw_available:false` explicitly means imported
products can be served, but rebuilding this frozen revision needs its raw inputs
materialized first. UpCloud retains the full profile. `--sources VERSION` can upgrade
a catalog to a fully verified local profile if receiver disk capacity permits.

The owned receiver now has **225 GiB free**. Imports copy only manifest-reachable
payloads and small reports; duplicate `vis.glb`, facade-program diagnostics and private
texture caches are omitted. Shared GLB dependencies arrive first. Exact farm validation
precedes atomic publication, acknowledgement and optional host pruning.

On **osgallery-dev**, the tested descriptor is already saved at
`/home/wnt/turku3d-local/loops/bulkgen2/profile-build.json`. Save a new descriptor from
`state/profile/build.json` if the final producer changes. It pins generation identity
independently of the host's mutable current-build report.

```sh
OPS=/home/wnt/turku3d-local/loops/bulkgen2
FARM=/home/wnt/turku3d-local/loops/tiles/farm
VERSION=4f7320b8e0f5f06b44e50a6cac59f7716f540a07b44783fb069e696b31df8516
python3 "$OPS/transfer.py" --root "$FARM" --catalog "$VERSION" \
  --link-dest "$OPS/farm/sources/versions/$VERSION"
python3 "$OPS/transfer.py" --root "$FARM" --build-file "$OPS/profile-build.json"
```

For streaming during the approved city run, start the receiver's own timer (not yet
started). It runs every two minutes without overlapping itself. No farm alias advances. Start it after the warmup record appears in city.log:

```sh
systemd-run --user --unit=bulkgen2-pull --on-active=1s --on-unit-active=120s \
  /usr/bin/python3 "$OPS/transfer.py" --root "$FARM" \
  --build-file "$OPS/profile-build.json" --prune
```

After the city result has no remaining tiles/failures, do one final pull and stop only
this timer with `systemctl --user stop bulkgen2-pull.timer`. Keep aliases unchanged
until the coordinator chooses adoption. Products are already in native
`products/<generation>/<tile>` layout with both base and source-bound revision metadata.

## Checks and operational evidence

`test_ops.py` covers immutable source links, catalog verification/materialization,
manifest-reachable imports, actual farm validation, prefetch exclusion, resume,
ack-bound deletion and preservation of unacknowledged generations under cache pressure.
`test_runtime.py` covers live limits, measured ordering, memory admission and idle exit.
Run inside the staged environment with `REPO` pointing to the tested checkout.
Python/JS/shell syntax checks and 26 cache/profiling + 4 Node texture tests also passed.
No visual feature changed: exact payload comparison supplies stronger evidence than
an arbitrary still/judge pair for this operational change.
