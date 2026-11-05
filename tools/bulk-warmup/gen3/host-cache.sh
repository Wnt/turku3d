#!/bin/bash
# Bulk host (root): mount osgallery-dev's farm, survey supply and pipeline env read-only over the WireGuard tunnel,
# through FS-Cache. The first read of a file crosses the network; later reads come from the local disk. Idempotent.
#   host-cache.sh up      cachefilesd on /var/cache/fscache, NFSv4.2 mounts with fsc, the farm overlay, local env copy
#   host-cache.sh status  mounts, cache size and FS-Cache counters (cookies, reads served from cache vs network)
#   host-cache.sh down    unmount (the cache directory stays; it survives reboots and plan changes)
#   host-cache.sh prefetch RESOLVED_JSON [GB]   warm the cache on the cheap plan, before a window: the source version,
#                         the products resolve.py names, then survey blocks nearest the city centre first, up to GB of
#                         blocks (default all). Reading a cached file again costs only local disk.
# Layout (paths mirrored 1:1 with osgallery-dev, so the pass and its records see the farm's own absolute paths):
#   /srv/osg/farm   <- 10.77.0.1:/home/wnt/turku3d-local/loops/tiles/farm   (ro, fsc)
#   /bulk/turku3d   <- 10.77.0.1:/bulk/turku3d                              (ro, fsc; the --supply survey blocks)
#   /home/wnt/turku3d-local/loops/tiles/farm = overlay(lower /srv/osg/farm, upper /srv/farm/upper): every write
#     (new tile products, checkpoints, pass records, linked survey blocks) lands on the local disk; sync-train.sh
#     --pull sends the new products back. work/, cache/, locks/ and logs/ are local directories (bind mounts), so
#     the host never reads the farm's live caches or locks.
set -euo pipefail
OSG=10.77.0.1
F=/home/wnt/turku3d-local/loops/tiles/farm
NFS_OPTS=vers=4.2,ro,fsc,hard,nconnect=4,rsize=1048576,actimeo=600,nocto
LOCAL_DIRS=(work cache locks logs)

bound() { grep -q turku3d-bulk /proc/fs/fscache/caches 2>/dev/null; }

up() {
  # The cache must be bound before the NFS mounts: a volume acquired without a cache never caches (the apt install
  # starts cachefilesd disabled, RUN=no, and the unit still reports active).
  if ! bound; then
    mountpoint -q /srv/osg/farm && down
    sed -i 's/^#\?RUN=.*/RUN=yes/' /etc/default/cachefilesd
    cat > /etc/cachefilesd.conf <<'EOF'
dir /var/cache/fscache
tag turku3d-bulk
brun 12%
frun 12%
bcull 8%
fcull 8%
bstop 4%
fstop 4%
EOF
    mkdir -p /var/cache/fscache
    systemctl enable -q cachefilesd 2>/dev/null; systemctl restart cachefilesd || true; sleep 2
    bound || { echo "cachefilesd did not bind a cache: journalctl -u cachefilesd"; exit 1; }
  fi
  mkdir -p /srv/osg/farm /bulk/turku3d /srv/farm/upper /srv/farm/ovl-work "$F"
  mountpoint -q /srv/osg/farm || mount -t nfs4 -o "$NFS_OPTS" "$OSG:$F" /srv/osg/farm
  mountpoint -q /bulk/turku3d || mount -t nfs4 -o "$NFS_OPTS" "$OSG:/bulk/turku3d" /bulk/turku3d
  if ! mountpoint -q "$F"; then
    mount -t overlay overlay -o lowerdir=/srv/osg/farm,upperdir=/srv/farm/upper,workdir=/srv/farm/ovl-work,redirect_dir=off,index=off,metacopy=off "$F"
  fi
  for d in "${LOCAL_DIRS[@]}"; do
    mkdir -p "/srv/farm/local/$d"; chown 1000:1000 "/srv/farm/local/$d"
    mountpoint -q "$F/$d" || mount --bind "/srv/farm/local/$d" "$F/$d"
  done
  chown 1000:1000 /srv/farm/upper
  # The pipeline env (venv and tools, ~2 GB in ~60k files) is not mounted: over NFS each small file costs round trips
  # (1 MB/s measured). stage.sh cache streams it once as a tar (79 s) with the farm runner shared-tiles-run.sh.
  status
}

status() {
  findmnt -t nfs4,overlay -o TARGET,SOURCE,FSTYPE | sed 's/^/  /'
  du -sh /var/cache/fscache /srv/farm/upper /srv/farm/local 2>/dev/null | sed 's/^/  /'
  df -h / | tail -1 | sed 's/^/  disk /'
  # Data reads served by the cache (Read ok=) against those fetched over the network (Retrv / netfs reads).
  grep -E '^(Cookies|Acquire|Read|Retrvl|IO|RdHelp|Stores|Cache)' /proc/fs/fscache/stats 2>/dev/null | sed 's/^/  /'
  grep -E '^(Reads|RA|ZeroOps|DownOps|CacheRd|CacheWr)' /proc/fs/netfs/stats 2>/dev/null | sed 's/^/  /'
}

prefetch() {
  local resolved=${1:?resolve.py output for the alias}; local limit_gb=${2:-100000}
  local log=/home/wnt/turku3d-bulk-gen3/logs/prefetch.log; mkdir -p "${log%/*}"
  read_all() { xargs -0 -r -P4 -n16 cat > /dev/null; }
  stamp() { echo "$(date -u +%FT%TZ) $* cache=$(du -sb /var/cache/fscache | cut -f1)" >> "$log"; }
  stamp start
  V=$(python3 -c "import json,sys; print(next(iter(json.load(open(sys.argv[1]))['source_versions'].values())))" "$resolved")
  find "/srv/osg/farm/${V#"$F"/}" -type f -print0 | read_all; stamp version
  python3 -c "import json,sys; print('\n'.join(json.load(open(sys.argv[1]))['products']))" "$resolved" | sort -u |
    while read -r g; do find "/srv/osg/farm/products/$g" -type f -print0 | read_all; stamp "products $g"; done
  # Blocks are named by their SW corner (E_N, 500 m); the centre is the pilot office block (E 23459668 N 6704254).
  find /bulk/turku3d/lidarpc/raw/pistepilvi -maxdepth 1 -name '*.laz' -printf '%s %p\n' | python3 -c "
import sys
rows = []
for line in sys.stdin:
    size, path = line.split(' ', 1); path = path.strip()
    e, n = path.rsplit('/', 1)[1][:-4].split('_')[:2]
    rows.append(((int(e) + 250 - 23459668) ** 2 + (int(n) + 250 - 6704254) ** 2, int(size), path))
total, limit = 0, float(sys.argv[1]) * 1e9
for _, size, path in sorted(rows):
    if total > limit: break
    total += size; sys.stdout.write(path + '\0')" "$limit_gb" | read_all
  stamp blocks
}

down() {
  for d in "${LOCAL_DIRS[@]}"; do mountpoint -q "$F/$d" && umount "$F/$d"; done
  mountpoint -q "$F" && umount "$F"
  for m in /bulk/turku3d /srv/osg/farm; do mountpoint -q "$m" && umount "$m"; done
  true
}

case "${1:-}" in
  up|status|down) "$1" ;;
  prefetch) shift; prefetch "$@" ;;
  *) sed -n 2,6p "$0"; exit 2 ;;
esac
