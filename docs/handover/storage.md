# storage handover: bulk HDD pool, what moved, open decisions

Status on 2026-11-04 01:35. Working files: `~/turku3d/loops/storage/` on the Mac (`STATUS.md`, `INVENTORY.md`,
`moves.log`, queue files, scripts in `bin/`). Logs on osgallery-dev: `~/turku3d-local/loops/storage/moves.log`.
Logs on pve-nvme: `/root/storage-agent/`. All queues have finished; nothing of this agent is running.

## Bulk pool layout

- **Pool:** `bulk` on pve-nvme (`ssh lab`, 192.168.1.126). One Seagate ST4000DM004 (4 TB SMR HDD), no redundancy.
  169G used of 3.6T.
- **`bulk/turku3d`:** lz4, atime=off, recordsize=1M, owner 1000:1000. Bind-mounted into osgallery-dev
  (CT 950, privileged, uids map 1:1) as `mp5: /bulk/turku3d,mp=/bulk/turku3d`. It was hot-plugged with no restart.
  - `/bulk/turku3d/<path relative to /home/wnt>`: moved from osgallery-dev. The old path is a symlink to it.
  - `/bulk/turku3d/mac/<path under ~/turku3d>`: moved from the Mac.
  - `/bulk/turku3d/README.txt` describes the layout.
- **`bulk/turku3d-protected`:** host only, not mounted in the container. Holds the products of generation 2e6515f4
  exactly as in the 12:23 SSD snapshot: 126921 files, 10.11 GB, checksum-verified. It is `readonly=on` with the
  snapshot `bulk/turku3d-protected@bulkgen2-2e6515f4-20261103`.

## What moved

Every move copied the data, ran an rsync `--checksum` dry run, compared file counts and bytes, and checked that no
process had the path open. Only then was the original replaced.

### osgallery-dev

The original path is now a symlink to the bulk copy, so every path keeps working. Restore with
`rm <path> && rsync -aHAX /bulk/turku3d/<rel>/ <path>/`.

| Path | Files | Apparent size |
|---|---:|---:|
| `~/turku3d/research` | 55265 | 8.67 GB |
| `~/turku3d-backups/tiles-28ba24e011a4a9c4-20261030` | 211605 | 18.04 GB |
| `~/turku3d-drafts` (retired drafts host; its user units are disabled) | 257168 | 49.74 GB |

### Mac

Destination is `osgallery-dev:/bulk/turku3d/mac/<path>`, 70.87 GB in total. Each source directory keeps a
`MOVED-TO-BULK.txt` with the location, the counts and its restore command:
`rsync -aH osgallery-dev:/bulk/turku3d/mac/<path>/ ~/turku3d/<path>/ && rm ~/turku3d/<path>/MOVED-TO-BULK.txt`.

- **`tasks/integration/reports/draft-reference-advisory`:** 20.55 GB.
- **Finished task reports:** 11.5 GB, from `tasks/{integration-data,ref-draft-bridges,ref-draft-ground-textures,
  facade-archetypes,ref-draft-wall-textures}/reports`.
- **`research/`:** 8.5 GB, from `streetview`, `prior-art`, `facade-cv` and `reconstruction`.
- **Old `profiling/` captures:** 3.6 GB, from the air-play 10-28 and 10-29 runs, `mac-profile` and `android-*`.
  - `evidence/`, `air-session` and `air-state` stay.
- **Video loop working directories:** 3.3 GB, from `video/2026-11-02/`.
  - The final `turku3d-before-after.mp4` and `share/` stay.
- **249 finished async-check snapshots** (`tasks/<task>/checks/<sha>`): 23.4 GB.
  - `repo/`, `browser/` and `work/` moved.
  - `request.json`, `result.json`, `logs/` and `worker.log` stay.
  - The cloned `node_modules` and `.venv` were not copied: they are APFS clones of a sandbox install and rebuildable.
    The frozen source is also git commit `<sha>`.
  - `codex-task checks` and `--fix-for` read only `wt/.registry/checks.jsonl`, so they are unaffected.
- **Staged, not deleted:** `private/gmaps-data`, a verified copy on bulk (1676986 paths; `du` 21.2G because of
  hardlinks). The local copy is kept.

Free space: Mac 7.1G before; 57G when the queue ended at 00:25; 50G now because other agents are writing.
osgallery-dev is unchanged, see below.

## Checking and stopping

Nothing is running now. To check the logs:

- Mac: `tail ~/turku3d/loops/storage/moves.log`
- osgallery-dev: `ssh osgallery-dev tail ~/turku3d-local/loops/storage/moves.log`

To run more moves:

- Mac: `~/turku3d/loops/storage/bin/mac-move.sh <path under ~/turku3d> [--copy-only] [--check]`. A queue is
  `bin/mac-queue.sh <file>` with one item per line. It runs one rsync stream, nice 10, IO throttle, 30 MB/s.
  - Stop a queue after its current item with `touch ~/turku3d/loops/storage/STOP`.
  - A rerun skips moved items.
- osgallery-dev: `~/turku3d-local/loops/storage/bin/osg-move.sh /home/wnt/<path>`, which copies, verifies, renames,
  symlinks and deletes.
  - Do not stop it with SIGSTOP: `sudo` then stays stopped. Kill the script instead; it never deletes before the swap.

## Open decisions for Jonni

1. **The 12:23 SSD snapshot `data/subvol-950-disk-0@bulkgen2-2e6515f4-20261103`.** It pins every block that existed
   on the osgallery-dev rootfs at 12:23, so moving data off the rootfs frees nothing while it exists.
   - It now holds **141G** of unique data. The farm's own churn since 12:23 adds to that, together with the 76 GB
     moved above. CT 950 has 28G available (the `data` pool is at 93%), and that keeps falling as the farm builds.
   - Its purpose, the original 2e6515f4 products, has a verified read-only copy in `bulk/turku3d-protected`.
   - Destroying it frees about 141G now: `zfs destroy data/subvol-950-disk-0@bulkgen2-2e6515f4-20261103` on
     pve-nvme. Not done, because snapshots are only destroyed with Jonni's OK.
2. **`private/gmaps-data` (Mac, 21.2G).** The verified copy is on bulk. Delete it locally? It is used by the private
   scorer (not running) and by the integrator advisory hook, which writes `requests/pending/`. Restore with
   `rsync -aH osgallery-dev:/bulk/turku3d/mac/private/gmaps-data/ ~/turku3d/private/gmaps-data/`.
3. **Other Mac candidates.** Unique sizes, so deleting them frees this much. Each still has a reader or an unclear
   owner:
   - `tools/cv`, 9.0G: Hugging Face weights, qwen 7G. `textures/turkutex/facade_cv.py` and `facade_models.py` read
     them, so ask first if facade CV is in use.
   - `tools/voice`, 3.5G: `bench/` 1.9G is eval output; `cache/` and `hf/` are model caches.
   - `data/cache`, 23G: old caches `tool-stages` 11G, `lidar` 8.7G and `buildings` 3.4G. `decoded/` is live.
   - Mac loops, 8.6G: `vfix12/work`, `vfix11/work` and `vfix/issues`. Their owners may resume them.
   - Finished worktrees, about 4.3G unique: `scripts/dev/wt rm` of 35 merged, clean, finished `wt/*` sandboxes and
     `wt/gmaps-validation-loop`. Keep `orch-deploy` and the unmerged ones (`fix-telemetry-hot`, `capture-params`,
     `facade-characterize-routes`).
   - Do not move `data/live-releases` or `data/tile-store`: they are APFS clones, about 0.1G unique.
4. **osgallery-dev candidates.** `loops/bulkgen2` 8.4G (its resume units failed), `loops/genspeed-*` 4G and vfix
   iteration directories (about 6G).
   - The farm itself (138G cache, 50G products) is hot and farm-owned. Its 141 unpinned generations (about 35G) need a
     farm-side retention rule. The pinned 2e6515f4 base (13.64G) and the aa2cf1ce migration (0.42G) stay on SSD.
5. **`copies=2`.** `zfs set copies=2 bulk/turku3d` makes ZFS repair bad sectors on new writes, at twice the space and
   write load. It does not protect against losing the disk.

## Risks

- **Single HDD.** `bulk` is one SMR disk without redundancy. The Mac data deleted after moving (70.87 GB) and the
  osgallery-dev originals that will be freed when the snapshot goes then exist only on this disk. ZFS detects
  corruption but cannot repair it with `copies=1`. A second disk, a mirror, or an off-host copy of
  `/bulk/turku3d/mac` would remove that risk.
- **SMR slowdowns.** Sustained small-file writes can slow the disk to tens of MB/s.
- **HDD speed.** The symlinked paths on osgallery-dev (`~/turku3d/research`, `~/turku3d-backups/...`,
  `~/turku3d-drafts`) now read at HDD speed. Report links into moved Mac directories need the restore command first.
