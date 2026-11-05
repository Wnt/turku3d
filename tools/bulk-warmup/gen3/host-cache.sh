#!/bin/bash
# Bulk host (root): mount osgallery-dev's farm and /bulk read-only over the WireGuard tunnel through FS-Cache. The first
# read of a large file crosses the network; later reads come from the local disk. Idempotent; bulk-cache.service runs
# `up` at every boot (a plan change is a stop and start).
#   host-cache.sh up | status | down     (down keeps the cache directory and the seeds; they survive reboots)
# Layout (paths mirrored 1:1 with osgallery-dev, so tools and records see the farm's own absolute paths):
#   /srv/osg/farm <- 10.77.0.1:/home/wnt/turku3d-local/loops/tiles/farm   (ro, fsc)
#   /srv/osg/bulk <- 10.77.0.1:/bulk/turku3d                              (ro, fsc: survey supply, the cities' raw)
#   /home/wnt/turku3d-local/loops/tiles/farm = overlay(seed /srv/farm/seed, NFS /srv/osg/farm; upper /srv/farm/upper)
#   /bulk/turku3d                            = overlay(seed /srv/bulk/seed, NFS /srv/osg/bulk; upper /srv/bulk/upper)
# The seeds are local copies of the hot small-file trees (stage.sh seed): an NFSv4 OPEN is a round trip FS-Cache cannot
# save (~20 ms with READ and CLOSE over the 5 ms tunnel), and a Turku lane opens every source-version WFS file of the
# city (~24k) per tile. Large files (survey blocks, rasters, stage caches, products) are not seeded: they fall through
# to NFS + FS-Cache on first use (window-warm.py reads the upcoming tiles' blocks ahead of the lanes).
# Every write lands in an upper layer on the local disk (new products, checkpoints, records, supplied survey blocks);
# sync-train.sh --pull sends products back. work/, locks/ and logs/ of the farm are local directories (bind mounts):
# the host never takes the farm's locks or reads its private work trees. The farm's shared content-keyed stage caches
# (cache/, 76 GB, mostly raw-derived lidar) stay in the overlay, so a spot rebuild here reads warm entries.
set -euo pipefail
OSG=10.77.0.1
F=/home/wnt/turku3d-local/loops/tiles/farm
B=/bulk/turku3d
NFS_OPTS=vers=4.2,ro,fsc,hard,nconnect=4,rsize=1048576,actimeo=600,nocto
READAHEAD_KB=16384   # the NFS default is 128 KB: one 1 MB READ in flight per stream
LOCAL_DIRS=(work locks logs)
OVL=redirect_dir=off,index=off,metacopy=off

bound() { grep -q turku3d-bulk /proc/fs/fscache/caches 2>/dev/null; }

nfs() {  # nfs EXPORT MOUNTPOINT
  mountpoint -q "$2" || mount -t nfs4 -o "$NFS_OPTS" "$OSG:$1" "$2"
  echo "$READAHEAD_KB" > "/sys/class/bdi/$(mountpoint -d "$2")/read_ahead_kb"
}

overlay() {  # overlay SEED NFS UPPER_ROOT TARGET
  mkdir -p "$1" "$3/upper" "$3/ovl-work" "$4"
  mountpoint -q "$4" ||
    mount -t overlay overlay -o "lowerdir=$1:$2,upperdir=$3/upper,workdir=$3/ovl-work,$OVL" "$4"
  chown 1000:1000 "$3/upper"
}

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
  mkdir -p /srv/osg/farm /srv/osg/bulk
  # A plain NFS mount left at /bulk/turku3d by an earlier layout must go before the overlay takes the path.
  [ "$(findmnt -no FSTYPE "$B" 2>/dev/null)" = nfs4 ] && umount "$B"
  nfs "$F" /srv/osg/farm
  nfs /bulk/turku3d /srv/osg/bulk
  overlay /srv/farm/seed /srv/osg/farm /srv/farm "$F"
  overlay /srv/bulk/seed /srv/osg/bulk /srv/bulk "$B"
  for d in "${LOCAL_DIRS[@]}"; do
    mkdir -p "/srv/farm/local/$d"; chown 1000:1000 "/srv/farm/local/$d"
    mountpoint -q "$F/$d" || mount --bind "/srv/farm/local/$d" "$F/$d"
  done
  # The pipeline env (venv and tools, ~2 GB in ~60k files) is not mounted either: stage.sh cache copies it once.
  status
}

status() {
  findmnt -t nfs4,overlay -o TARGET,SOURCE,FSTYPE | sed 's/^/  /'
  du -sh /var/cache/fscache /srv/farm/seed /srv/bulk/seed /srv/farm/upper /srv/bulk/upper /srv/farm/local 2>/dev/null |
    sed 's/^/  /'
  df -h / | tail -1 | sed 's/^/  disk /'
  # IO rd = reads served from the cache, wr = data written into it after a network read.
  grep -E '^(Cookies|Acquire|IO)' /proc/fs/fscache/stats 2>/dev/null | sed 's/^/  /' || true
}

down() {
  for d in "${LOCAL_DIRS[@]}" cache; do mountpoint -q "$F/$d" && umount "$F/$d"; done
  for m in "$F" "$B" /srv/osg/bulk /srv/osg/farm; do mountpoint -q "$m" && umount "$m"; done
  true
}

case "${1:-}" in
  up|status|down) "$1" ;;
  *) sed -n 2,5p "$0"; exit 2 ;;
esac
