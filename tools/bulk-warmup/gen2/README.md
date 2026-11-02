# Gen 2 staging on UpCloud — 2026-11-02

This replaces the production warm-up workflow for the local Gen 2 round. Only the
coordinator operates the UpCloud API. Keep the small plan until Jonni explicitly
approves resizing. Do not run the old hold, push-to-OCI, snapshot, or production scripts.

Current host: `root@185.20.139.132`, root `/root/turku3d-bulk-gen2` (300 GB MaxIOPS).
Mac state: `~/turku3d/loops/bulkgen2`. This run is **staging, not READY**: acquisition is
running. The farm-compatible runner and verified pull tools are implemented and tested
offline; real proof builds and their cross-host comparison remain.

## Plan and provenance

`plan.py` uses the already retained OSM Turku boundary (relation 399906, ODbL 1.0)
and the previous round's `state/walk.json`. It produces:

- 5,245 municipality L0 cells; union with the 995-cell old walk: **5,812 candidates**.
- 7,003 L0 input cells after a 500 m halo. This covers neighbouring spot heights,
  roof/kerb lidar, imagery and other input-key dependencies before any build.
- 2,825 source-direct 1 km far-scene cells in a 15 km municipality buffer.
- `tile-map.png`, `plan.json`, `walk.json`, `input-tiles.json`, `far-cells.json`.

The published city lidar index supports 5,234 candidates; 578 have no city ground
coverage. Keep these explicit in coverage reports; regional terrain does not imply
that the full collision generator can build them.

The initial code archive is integration commit `e05e7a9370a5ce1bc52beb3d859037c1687101ff`,
producer generation `f979f621e19dfb98dfe2015613967a194dd7b9fcbc9a1c052840dc14aa84de37`.
Select another committed revision from the Mac, after acquisition/build workers stop:

```sh
python3 tools/bulk-warmup/gen2/pin.py local/loop-integration
```

This sends `git archive`, records the producer identity, and atomically selects its
`repo` symlink. It never starts a build, changes sources, or resizes the server.
Recheck acquisition coverage if the chosen code changes its source requirements.

## Environment and transfers

1. Put an integration archive in `ROOT/repo`; copy `bootstrap-x86.sh` and `run.sh` to
   ROOT. Run bootstrap as root. It installs the existing linux-64 micromamba lock,
   Python 3.12.16, Node 24.18.0, KTX 4.4.2, meshoptimizer 1.3.0 and pinned npm tools.
   Run scripts with absolute paths: `run.sh` changes directory to the code snapshot.
2. As explicitly authorized, create OCI's `~/.ssh/bulk_upcloud` if absent and append
   its public key to the staging host's authorized keys. No private key is copied.
3. On the Mac, run the following in a persistent terminal/process. Its two streams
   read OCI at nice 19 / ionice idle, transfer directly to UpCloud and keep logs on
   the Mac. SSH uses the Mac's verified host key through KnownHostsCommand; OCI
   needs no additional files. Failed streams return nonzero and rsync resumes.

   ```sh
   python3 tools/bulk-warmup/gen2/sync-oci.py \
     --state "$HOME/turku3d/loops/bulkgen2/oci-sync"
   ```

   The successful initial copy took 326 seconds for raw inputs. Source categories:
   raw LAZ 4.3 GiB, ortho 9.0 GiB, OSM 960 MiB, WFS 3.6 GiB, streetview 108 MiB;
   textures about 3 GB plus facade approvals/assets/characterization. No legacy
   stages or global hash index are copied: their absolute paths are unsuitable.
4. Compare names/sizes on osgallery-dev before deciding what to send. The retained
   inventory is `source-inventory.json.gz` on the Mac, not in git. The farm's
   `sources/` directory was copied with **rsync -a, without -L**: 435,893,459 bytes
   of regular files (overlays, registries, metadata, one lidar supplement). Facade
   directories matched OCI with zero additional rsync file bytes. Do not copy the
   4.4 GB horizon product tree or the 17 GB classified lidar over this uplink.
5. Run `relocate.py ROOT` after copying overlays. It remaps source symlinks and
   rewrites absolute VRT references to relative references, without copying caches.
   Some historical source profiles reference absent archive/cache markers or
   public WFS areas; these are reported, not silently treated as complete. Use a
   verified final profile for builds. No OCI paths are mirrored with host symlinks.

## Public acquisition (currently running)

`size-lidar.py STATE` HEAD-inventories all required published lidar URLs first.
All 2,564 URLs succeeded: 1,282 classified units = **183,058,782,305 bytes** and
1,282 ground units = **1,643,430,947 bytes**. This is much larger than the old walk.

`prepare-regional.py --plan STATE/plan.json --out ROOT/regional-city` rebuilds the
regional grid directly from Copernicus DEM and ESA WorldCover COG windows. The
old 40 km grid cannot cover northern Turku. The staged 64 × 84 km grid covers
local extent `[-32000,-52000,32000,32000]`, has 67,432,333 compressed bytes and
SHA-256 `7d3428185d56647f307ae7a818617a70f25ed2e3514b3b72f11eccb48856c476`.
Attribution and public URLs are in its `sources.json`; no regional bytes were
uploaded from osgallery-dev.

After OCI's raw transfer finishes, run on UpCloud:

```sh
cd /root/turku3d-bulk-gen2
nohup ./run.sh python /root/turku3d-bulk-gen2/prefetch.py \
  /root/turku3d-bulk-gen2/state --workers 3 \
  > state/prefetch.log 2>&1 < /dev/null &
echo $! > state/prefetch.pid
```

There are **6,371 jobs**: 1,282 ground, 1,282 classified, 1,831 WFS,
1,831 imagery and 145 deduplicated OSM clips. The WFS recipe includes the farm's
required and optional layers plus building registers and Gen 2 breakline/structure
layers. Acquisition uses the pipeline fetchers, a checksum registry per unit and
one final registry merge. It does not serialize network requests under a global
registry lock. Gen 2 already uses per-source fingerprint locks for builds.

Successful receipts record produced file sizes. A missing file invalidates the
receipt. Failures are retained in `prefetch-progress.jsonl` and rerun on resume.
`--limit`/`--unit` probes never publish a completed-prefetch marker. Three workers
are appropriate for the small host; the 35 GiB free-space floor stops new fetches.
No builds should start before `PREFETCH_COMPLETE.json` exists and its coverage
report has been reviewed. Do not fetch more sources during a build generation.

Monitor with bounded reads:

```sh
ssh root@185.20.139.132 'tail -5 /root/turku3d-bulk-gen2/state/prefetch-progress.jsonl; df -h /'
```

## Evidence and remaining work

The staged environment imports PDAL/rasterio/shapely/triangle/jsonschema. Ground
(3 units), WFS, imagery and OSM fetch probes all succeeded. The WFS probe took
7.82 s, imagery 2.56 s and an OSM clip 24.17 s; these are **acquisition**, not build
timings. Offline registry checks prove baseline immutability, independent locks
and SHA verification; run with:

```sh
/root/turku3d-bulk-gen2/run.sh python /root/turku3d-bulk-gen2/test_prefetch.py
```

Next: finish and audit coverage, freeze source identity/supplements, prove 2–3
tiles on one worker, and compare farm payloads and input identities across hosts. Estimate
64-core time only from those Gen 2 builds, discounting 13–43% CPU steal. The raw
source footprint will leave limited headroom on 300 GB: bound stage caches and
pull/validate completed products before reclaiming their host copies. Never trim
an active tile's stages or any unacknowledged finished product. No city build or
READY-to-resize message has been issued yet.


## Freeze, prove and pull

The new tools are staged at `ROOT/ops/`. The acquisition already running still uses
its original root-level script; do not overwrite or restart it merely to update tools.
After it finishes, use the newer prefetch script for any retries. Receipts now include
an acquisition-recipe hash: changing a source requirement invalidates old receipts,
while renderer/geometry-only edits leave source acquisition reusable. The initial
run's unchanged pinned recipe was recorded in `state/acquisition.json` while it ran.

On UpCloud, after reviewing the completed coverage (no builds before this):

```sh
ROOT=/root/turku3d-bulk-gen2
"$ROOT/run.sh" python "$ROOT/ops/freeze.py" "$ROOT"
"$ROOT/run.sh" python "$ROOT/ops/bulk.py" "$ROOT"   --tiles t0_0_0 t0_1_0 t0_-1_0 --workers 1
```

`freeze.py` makes VRT references relative with atomic replacements, then hard-links
all input files into `farm/sources/versions/<content-hash>`. It carries per-file byte
counts/SHA-256 values, preserving known download checksums and hashing relocated
wrappers/authored data. This does not duplicate the 183 GB lidar. Subsequent atomic
source updates cannot alter the retained profile. `state/sources.json` reports the
logical size by group and the acquisition identity.

`bulk.py` imports the selected revision's actual `shared_tiles.Farm`: the same build
command, generation/source binding, cache mounts, collision and GLB validation, and
atomic `ready.json` publication as the LAN farm. Completed or acknowledged tiles
resume without rebuilding. Products go to `farm/products/<generation>/<tile>`.
The batch holds the source lock and refuses unprefetched tiles or a changed fetch
recipe. `state/bulk-result.json` includes CPU/wall seconds, CPU steal, peak child RSS,
stage/cache metrics and failures. SIGTERM stops admission and finishes owned active
workers. Only this dedicated host gets a higher thread-pool limit; LAN limits stay
unchanged.

The existing farm cache eviction runs under pressure: per-key locks protect stage
entries, active jobs pin their inputs, and raw-derived caches are reclaimed only
when all workers have finished reading them. The default 25 GiB floor and 1 GiB
reservation per active worker stop admission before the disk fills. Exit 75 means
pull/acknowledge finished output (or finish an interrupted batch), then resume.
No unacknowledged tile is deleted.

A provisional command for the eventual **64-vCPU plan**, after Jonni approves resize
and the coordinator selects the revision, is below. Worker count/reservation must
be finalized from the real proofs and remaining disk space; this has not been run:

```sh
ROOT=/root/turku3d-bulk-gen2
"$ROOT/run.sh" python "$ROOT/ops/bulk.py" "$ROOT"   --walk "$ROOT/state/walk.json" --workers 48 --reserve-gib 1
```

48 workers reserve 73 GiB including the floor. If space is tighter, lower concurrency
or pull output; never lower the 25 GiB floor. Merely selecting a 64-core plan does not
make 64 concurrent scratch-heavy builders safe on this disk.

Run the pull on **osgallery-dev**, initially into the owned proof farm. Copy only
`transfer.py` there from the Mac. Read VERSION from the host's `state/sources.json`:

```sh
python3 /home/wnt/turku3d-local/loops/bulkgen2/transfer.py   --root /home/wnt/turku3d-local/loops/bulkgen2/farm   --sources VERSION   --link-dest /home/wnt/turku3d-local/combined/data
python3 /home/wnt/turku3d-local/loops/bulkgen2/transfer.py   --root /home/wnt/turku3d-local/loops/bulkgen2/farm   --tiles t0_0_0 t0_1_0 t0_-1_0
```

Sources move host → osgallery-dev; rsync checksum/link-dest can reuse identical local
files. The source manifest is then verified against **every received SHA-256**.
Space admission reserves 25 GiB and an active transfer stops at a 26 GiB warning
floor, retaining partial downloads. Source verification receipts are required before
products are published. No source download writes `combined/data/raw`.

The product pull copies shared objects first, validates the entire manifest/GLB
reference graph using the producer's farm validator, and atomically installs each
tile under the farm's generation/tile lock. It refuses to overwrite a differing
existing farm product. The producer receives a durable acknowledgement containing
the verified manifest hash. Adding `--prune` removes only the corresponding host
tile after rechecking that hash and generation under its own tile lock. This is the
streaming disk strategy for the full round; raw sources and shared assets remain.

After the owned proof farm has passed comparison, the same tool can target
`/home/wnt/turku3d-local/loops/tiles/farm`; use the owned frozen profile as `--link-dest`
to avoid duplicating its bytes. It installs farm-native revision metadata and ready
products, without advancing the integration alias or touching production.

Eight offline checks now pass: baseline registry immutability and independent locks;
source snapshot immutability and checksum rejection; portable VRTs retaining prior
hard links; recipe invalidation; actual farm scheduling/resume with synthetic payloads;
prefetch/build exclusion; corrupt shared payload rejection; and acknowledgement-bound
pruning. These checks do **not** substitute for the outstanding real tile proofs.

```sh
/root/turku3d-bulk-gen2/run.sh python /root/turku3d-bulk-gen2/ops/test_ops.py
/root/turku3d-bulk-gen2/run.sh python /root/turku3d-bulk-gen2/ops/test_prefetch.py
```
