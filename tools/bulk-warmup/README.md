# Bulk warm-up on a rented host

Builds the whole warm-up area on a large rented server with the exact generator OCI runs, and
streams the finished tiles into the OCI tile store, so OCI only serves warm files. First used on
2026-10-30 before the LAN party: 1,128 tiles built in 36 minutes on 64 cores, for about €1.5.

## Run book

Order matters; each step names where it runs. `BULK_HOST=root@<ip>` throughout.

1. **Prefetch the whole area's raw inputs on OCI first.** A tile's input key covers neighbouring
   spot-height files, so tiles built before a neighbour's inputs arrive go stale later.
   `./run.sh python tools/bulk-warmup/prefetch-area.py <walk.json>` (resumable, about 20 min for
   ~400 units; log `~/prefetch-area.jsonl`). The walk comes from `idle.route_walk` (step 5).
2. **Create the host** (orchestrator; costs money, confirm plan and price first). UpCloud
   `CLOUDNATIVE-2xCPU-4GB` + a 150 GB MaxIOPS disk in fi-hel1 to set up, then stop, change plan
   and start: the account's 300 GB memory quota caps it at **64 cores**
   (`CLOUDNATIVE-64xCPU-256GB`, €1.22/h); the 80-core plan needs 512 GB. Token: `~/.config/up_token`,
   never printed. Login key: the Mac's `~/.ssh/id_rsa` as `root`.
3. **Give OCI direct SSH to the host** (fastest path, 7.5 ms and about 140 MB/s per stream):
   create `~/.ssh/bulk_upcloud` on OCI and append its public key to the host's
   `/root/.ssh/authorized_keys`.
4. **Copy the source and inputs from OCI** (on OCI): `rsync -a ~/turku3d-drafts/repo/` to
   `$BULK_HOST:/root/turku3d-bulk-warmup/repo/` (OCI's source is a snapshot, not a git checkout,
   and GitHub may lag), then `BULK_HOST=… tools/bulk-warmup/sync-to-bulk.sh` (about 12 min for
   ~45 GB). Run `hold-prebuild.sh` meanwhile: the OCI data volume (~80 MB/s) is the bottleneck.
5. **Bootstrap the host**: `bootstrap-x86.sh` (about 6 min). Copy `walk.json` (from `route_walk`)
   and `skip.json` (tiles OCI already has at the current generator) to
   `/root/turku3d-bulk-warmup/state/`. Check that `turku3d.coarse.packaging.current_code()` matches
   OCI's `/coarse/health` `prebuild.generator`.
6. **Prove one tile end to end** on the small plan: `bulk.py <state> 2`, the pull loop on OCI,
   then `check-pulled.sh` (no rebuilds, HTTP 200 from the public URL).
7. **Resize and run**: stop, change plan, start; run everything under OCI's paths:
   `cd /home/ubuntu/turku3d-drafts && TURKU3D_DRAFT_HOME=/home/ubuntu/turku3d-drafts
   repo/tools/remote-drafts/run.sh python bulk.py state 60`, `prehash.py` once, and
   `trim-stages.sh` in the background. On OCI: `BULK_HOST=… pull.sh`.
8. **Finish**: `touch state/ALLDONE`; wait for `pull complete`; sync `shared/` and new `data/raw/`
   once more; delete the server **with its disk** (`DELETE /server/<uuid>?storages=1`); remove
   `~/.ssh/bulk_upcloud` on OCI; `rm ~/hold-prebuild.on`.
9. **Protect the result**: `sudo cp -al` the tiles and `shared/` into
   `/srv/turku3d/snapshots/<name>/` and `chmod 555` **only the snapshot's directories**. Never
   `chown`/`chmod` the files: they are hard links, so that changes the live tiles too.

## Scripts

| Script | Runs on | Purpose |
| --- | --- | --- |
| `prefetch-area.py` | OCI | Fetch every missing raw unit of the walk before building |
| `sync-to-bulk.sh` | OCI | Parallel direct copy of raw data and caches to the host |
| `hold-prebuild.sh` | OCI | Keep the OCI idle prebuild at one worker during the copy |
| `bootstrap-x86.sh` | host | x86 pipeline environment and OCI path mirroring |
| `bulk.py` | host | Full-pass builds of the walk with N persistent workers; resumable |
| `prehash.py` | host | Fill the input-hash index in parallel |
| `prune_index.py` | host | Drop finished tiles from the input-hash index |
| `trim-stages.sh` | host | Delete stage caches of finished tiles to save disk |
| `pull.sh` | OCI | Pull finished tiles (shared textures first) and swap them in atomically |
| `check-pulled.sh` | OCI | Request random pulled tiles and report any rebuild |
| `setup-host-from-oci.sh` | host | Set up a non-root host that can SSH to OCI itself (e.g. osgallery-dev) |
| `push-to-oci.sh` | host | Push finished tiles from such a host into OCI (counterpart of `pull.sh`) |
| `oci-trim-stages.sh` | OCI | Every 10 min, drop stage caches of finished current tiles (keeps the warm-up above its 15 GB disk stop) |

## What we learned on 2026-10-30

- **Absolute paths.** Stage caches and the input-hash index store OCI paths
  (`/home/ubuntu/turku3d-drafts/...`). Without the mirror symlinks, packaging failed (missing
  `facade-kit` textures) and every worker re-hashed 16 GB of raw data.
- **The input-hash index is a global lock.** `tile_layer.fingerprints` rewrites one JSON index
  (up to 21 MB) under one lock for every miss; 40–50 of 60 workers queued behind it. `prehash.py`
  and `prune_index.py` raised the rate from about 30 to 81 tiles/min. A generator-side fix would
  change the generator version, so it was not made during the freeze.
- **Downloads serialize too.** WFS fetches share one registry lock (about 3 min per unit), which
  idled the host on the outer ring. Hence step 1: prefetch everything first.
- **Stale input keys.** UpCloud fetched neighbour spot heights after building inner tiles; 577 of
  the 1,393 tiles went stale on OCI. OCI keeps serving them and rebuilds them in the background.
- **Shared textures.** About 700 new content-addressed `shared/*.ktx2` files existed only on the
  host; tiles must not be swapped in before them.
- **Output.** x86 builds have the same generator version, input key, sizes and counts as OCI's
  arm64 builds; only the GLB content hash differs (floating point). Accepted.
- **CPU steal** on the shared CloudNative plan was 13–43 %, so 64 vCPUs gave roughly 38–55 cores.
- **OCI disk.** The panorama cache stores content in `streetlevel/.objects`; deleting the named
  files frees nothing until blobs with one link are removed (`find .objects -links 1 -delete`).
- **Process control.** `pkill -f`/`pgrep -f` with your own pattern also matches your own shell;
  stop processes by pid file or parent pid.

## Build failures to decide on later

315 tiles were `NoCoverage` (outside the lidar or ground data): expected. The rest, by error
(the five `FileNotFoundError`s are a cold-start race on the shared surface library):

| Tiles | Error | Examples |
| --- | --- | --- |
| 117 | `KeyError: 'Katuosalaji'` (outer WFS street areas lack the field) | t0_-10_-15, t0_-11_4, t0_-12_4 … |
| 11 | `ValueError: Kerb seam needs ground inputs for <neighbour>` (neighbour without ground) | t0_-14_-1, t0_3_-15, t0_-2_-17, t0_16_-13 … |
| 5 | `FileNotFoundError` in `.surface-library-*` (concurrent first build) | t0_-8_6, t0_8_-3, t0_9_-1 … |
| 2 | `GEOSException: Unable to determine overlay result geometry dimension` | t0_3_1, t0_-6_5 |
| 2 | `GEOSException: TopologyException: side location conflict` | t0_1_15, t0_1_16 |
| 1 | `AttributeError: 'NoneType' object has no attribute 'is_empty'` | t0_-3_12 |

## Cost

Small plan about 1.5 h (€0.03), 64-core plan about 45 min (€1.22/h), disk about 2 h (€0.05/h):
about €1.5 in total.

## Extra capacity: osgallery-dev

osgallery-dev (a Proxmox container 950 on `lab`, x86) can SSH to OCI with `~/.ssh/oci_cuttlefish`, so it
runs `setup-host-from-oci.sh` itself (about 15 min, 33 GB in `~/turku3d-bulk-warmup`, never touching the
legacy `~/turku3d-drafts`), then `bulk.py` with nice'd workers on the walk reversed (outer tiles first,
while OCI works outward) and `push-to-oci.sh`. Its cores can be raised live on lab:
`pct set 950 -cores 16` (done 2026-10-30, from 10).
