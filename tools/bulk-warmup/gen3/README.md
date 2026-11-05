# gen3: a rented host for a city-wide migration window

This runs a migrated alias's city pass (`tile_city_pass.py`, the same chain as on the osgallery-dev farm) over all
city tiles on a 64-core UpCloud host, inside one 55-minute window.

There is no persistent UpCloud storage between runs: each run creates the host, stages it from osgallery-dev, and
deletes it with its disk afterwards. The 64-core resize needs Jonni's explicit OK.

## Stage a host (Mac)

```sh
tools/bulk-warmup/gen3/stage.sh all --train train4 --smoke-tile t0_-1_-1
```

State goes to `$BULK_STATE` (default `~/turku3d/bulk-gen3`): `server.json`, `manifest.json` and the verify output.
`server.json` is the only source of the host's uuid, IP and plan: `window.py`/`upcloud.py` and `sync-train.sh` read
the copy that `bootstrap` puts on osgallery-dev. The UpCloud token is `~/.config/up_token` and is never printed.

| Step | What | 2026-11-04 |
|---|---|---|
| `create [--disk GB]` | `CLOUDNATIVE-1xCPU-4GB` (€0.018/h, billed only while on) and a Standard disk (default 260 GB, €0.031/h), fi-hel1, Ubuntu 24.04 | 2 min |
| `bootstrap` | user `wnt` (uid 1000), 8 GB swap file, `../gen2/ttl` kit, host scripts, shallow `$TIP` checkout (default `local/loop-integration`); tools and `server.json` to osgallery-dev | 2 min |
| `upload --train A` | `upload.sh` on osgallery-dev, nice 19 / ionice idle, paths mirrored 1:1 | 225.8 GB, 45 min, 83.6 MB/s |
| `verify` | catalog and SHA-256 check on the host; writes `manifest.json` | about 15 min |
| `smoke --train A --smoke-tile T` | the alias's whole chain on T from the base, compared with osgallery-dev's product of T | 226 s on 1 vCPU, byte-identical |

What `upload.sh` sends is derived from the farm's records by `resolve.py`, not hard-coded:
- the aliases' source version;
- every survey block: lidarpc's `/bulk` supply plus the blocks only the version has. The version's other blocks are
  hard-linked on the host.
- the product generations: base, steps, spot rebuilds and finished tiles, in one rsync so hard links are kept;
- the code snapshots, farm metadata and the pipeline env.

Choosing the source and the disk:
- **Source.** One stream from osgallery-dev gives about 78 MB/s, and 3 streams about 100 MB/s (the home uplink).
  OCI to UpCloud was measured at 43 MB/s on one stream and 28 MB/s on four, and OCI holds none of these inputs except
  23 survey blocks.
- **Disk tier.** Standard, at 38% of MaxIOPS's price: fio gave 370 MB/s write, 576 MB/s read, and 14.8k/3.4k random
  IOPS.

Expected verify findings:
- Paths the farm changes after the copy show as sums failures, for example a live spot rebuild or a base tile being
  built. `sync-train.sh` mirrors them with `--delete` before the window.
- `raw/laz/maanpinta/23460500_6706500.laz` differs from its catalog entry on osgallery-dev too.

## A cached host instead of an upload (2026-11-05)

`stage.sh cached` (create, bootstrap, cache), then `stage.sh seed --train A [--city C ...]`. No full upload: the host
reads osgallery-dev's farm and `/bulk` on demand.

- **Tunnel.** osgallery-dev (behind the home NAT) runs WireGuard `wgbulk` 10.77.0.1 to the host's `wg0` 10.77.0.2 (UDP
  51820, accepted from the home uplink only; host nftables: ssh, the tunnel, nothing else). Egress is capped at
  480 Mbit/s (tbf on `wgbulk`), about 70% of the 84 MB/s the uplink gave with 4 streams, so city.lab keeps headroom.
  Path MTU is 1500, so `wg0`'s 1420 is right.
- **NFS.** Kernel nfsd in the osgallery-dev container (privileged LXC), NFSv4.1/4.2 only, listening on 10.77.0.1 only,
  16 threads, read-only exports of the farm and `/bulk/turku3d` to 10.77.0.2 only (`/etc/exports.d/turku3d-bulk.exports`).
  ZFS ignores ionice; the thread count and the tunnel cap bound its load.
- **FS-Cache.** The host mounts with `fsc,nconnect=4,rsize=1M,actimeo=600,nocto` and 16 MB readahead; `cachefilesd`
  caches file data on the host disk and survives reboots and plan changes. Measured on a 100 MB survey block: cold
  51-70 MB/s (one stream), 59 MB/s with 4 streams at the cap; warm re-reads with the page cache dropped 413-724 MB/s
  and 0 bytes on the tunnel.
- **Seeds.** An NFSv4 OPEN is a round trip FS-Cache cannot save (OPEN + READ + CLOSE about 20 ms), and a Turku lane
  opens every source-version WFS file of the city (~24k) per tile. So the hot small-file trees are copied once into
  local seed layers under the overlays (`stage.sh seed`, direct rsync from osgallery-dev over the tunnel): the
  source version without its survey blocks (17 GB with the code snapshots), and per city its checkout, data root and
  raw files under 4 MB. Large files are not copied; they come through the cache on first use.
- **Writes.** The farm and `/bulk` are overlays at their own paths (seed and NFS lower, local upper), so tools and
  records see osgallery-dev's absolute paths and every write stays on the host disk. `sync-train.sh --pull` reads only
  the farm overlay's upper layer, skips whiteouts and sends each tile's `ready.json` in a second rsync after its files.
- **Spot rebuilds.** `host-farm.sh start N` runs osgallery-dev's deployed `shared_tiles.py` on 127.0.0.1:7950 against
  the overlay (empty queue, fresh event log), so a pass's Rebuild requests build on the host; the farm's shared stage
  caches are read through the cache.
- **Order and read-ahead.** `window-warm.py` supplies the survey blocks of upcoming tiles ahead of the lanes with the
  pass's own verified copy. `window-cities.py` builds other cities' tiles ring by ring round each centre, interleaved
  across cities and in step with the Turku pass's ring; each tile is renamed into place only when complete.

Pitfalls met: apt starts `cachefilesd` disabled (RUN=no) while the unit reports active, so a mount made before the
cache is bound never caches (`host-cache.sh up` checks `/proc/fs/fscache/caches`); a tar relayed through the Mac was
cut twice by a corrupted packet (sshd: "message authentication code incorrect"), hence the direct rsync; the image
had no `node` (bootstrap installs osgallery-dev's NodeSource version, pinned); unattended-upgrades took the single
vCPU (bootstrap disables the apt timers).

## The window (only with Jonni's OK)

1. Jonni creates the restricted sub-account credential at `~/.config/turku3d/upcloud-ttl` on osgallery-dev.
   Without it, `window.py up` refuses to resize.
2. `stage.sh grow 340`: room for results (about 31 GB for the city) and the decoded-LAZ cache.
3. On osgallery-dev, `~/turku3d-local/loops/bulkgen3/bin/sync-train.sh ALIAS`. Finish the alias's spot rebuilds on
   the farm first: the host has no farm daemon.
4. On osgallery-dev, `cd ~/turku3d-local/loops/bulkgen3/window && python3 window.py up`. It resizes to 64 cores,
   arms the 55-minute backstop and checks the in-server TTL.
5. On the host, `LANES=56 BLOCKS=2 /home/wnt/turku3d-bulk-gen3/window-pass.sh ALIAS`.
   - Memory limits lanes: 2.4 GB at `--blocks 1`, 6–8 GB at 9.
   - `trim-decoded.py` deletes least-recently-used decoded blocks under their locks when free disk falls below 30 GB.
   - For a mid-run tweak, kill the pass's process group and rerun: finished tiles are kept.
6. On osgallery-dev, `bin/sync-train.sh ALIAS --pull` (new files only, hard-linked to the base), then
   `window.py down`.
7. On the Mac, `stage.sh delete`. It removes the host with its storage, and only the uuid in `server.json`, never
   `vm-control`.

## Files

| File | Runs on | Purpose |
|---|---|---|
| `stage.sh` | Mac | create, bootstrap, upload, verify, smoke, grow, delete |
| `resolve.py` | osgallery-dev | aliases to source version, product generations, code revisions |
| `upload.sh` | osgallery-dev | parallel staging rsync |
| `sync-train.sh` | osgallery-dev | pre-window catch-up and post-window pull of one alias |
| `window.py`, `upcloud.py` | osgallery-dev | 64-core window up/down with the 55-minute backstop, locked to `server.json`'s server |
| `window-pass.sh` | host | the alias's city pass at window settings, as `wnt` |
| `trim-decoded.py` | host | keeps the decoded-LAZ cache from filling the disk |
| `host-cache.sh` | host | NFS + FS-Cache mounts, seed/overlay layout (bulk-cache.service at boot) |
| `host-farm.sh` | host | the farm service on 127.0.0.1:7950 for spot rebuilds |
| `window-warm.py` | host | survey blocks of upcoming tiles supplied ahead of the lanes |
| `window-cities.py` | host | other cities' first+full builds, ring-interleaved, atomic per tile |
| `verify_stage.py` | host | catalog and SHA-256 verification of the staged inputs |
