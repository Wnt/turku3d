#!/bin/bash
# osgallery-dev: stage the inputs of the given migrated aliases onto a fresh bulk host. Paths are mirrored 1:1.
#   upload.sh HOST_IP ALIAS [ALIAS ...]      (env: LAZ_STREAMS=2, SUPPLY=lidarpc's pistepilvi dir)
# Sends the aliases' source version, every survey block (lidarpc's supply plus the blocks only the version has;
# the version's other blocks are hard-linked on the host), the product generations resolve.py names (one rsync, so
# hard links between them are kept), the code snapshots, farm metadata, and the pipeline env.
# Read-only here: nice 19 + ionice idle. Rerunnable: rsync's size+mtime check skips what already arrived.
# Measured 2026-11-04: 225.8 GB in 45 min (83.6 MB/s, 3 streams, the home uplink); OCI -> UpCloud was slower.
set -u
IP=${1:?host ip}; shift; [ $# -ge 1 ] || { echo "aliases?"; exit 2; }
H=root@$IP
HERE=$(cd "$(dirname "$0")" && pwd)
D=$HOME/turku3d-local/loops/bulkgen3
F=/home/wnt/turku3d-local/loops/tiles/farm
SUP=${SUPPLY:-/bulk/turku3d/lidarpc/raw/pistepilvi}
N=${LAZ_STREAMS:-2}
mkdir -p "$D/logs"; cd "$D" || exit 1
python3 "$HERE/resolve.py" --farm-root $F "$@" > resolved.json || exit 1
V=$(python3 -c "import json; print(next(iter(json.load(open('resolved.json'))['source_versions'].values())))")
V=${V#"$F"/}
mapfile -t PRODUCTS < <(python3 -c "import json; print('\n'.join('products/' + g for g in json.load(open('resolved.json'))['products']))")
mapfile -t CODES < <(python3 -c "import json; print('\n'.join('revisions/' + c + '/' for c in json.load(open('resolved.json'))['codes']))")
export RSYNC_RSH="ssh -i $HOME/.ssh/bulk_gen3 -c aes128-gcm@openssh.com -o ServerAliveInterval=30 -o StrictHostKeyChecking=accept-new"
R="nice -n19 ionice -c3 rsync -aH --numeric-ids --partial-dir=.rsync-partial --stats"
find "$SUP" -maxdepth 1 -name '*.laz' -printf '%f\n' | sort > supply.txt
rm -f supply-part.*; split -n r/"$N" -d supply.txt supply-part.
find "$F/$V/raw/laz/pistepilvi" -maxdepth 1 -name '*.laz' -printf '%f\n' | sort > version-laz.txt
comm -23 version-laz.txt supply.txt > version-only-laz.txt
stamp() { echo "$(date -u +%FT%TZ) $*" >> "$D/upload.log"; }
s1() {
  stamp s1 start; cd $F || return
  $R -R aliases.json pin.json fallbacks.json migrations "$H:/" > "$D/logs/s1-meta.log" 2>&1 &&
  $R -R --include='/revisions/' --include='/revisions/*/' --include='/revisions/*/*.json' --exclude='*' ./ "$H:$F/" >> "$D/logs/s1-meta.log" 2>&1 &&
  $R -R "${CODES[@]}" "$H:$F/" >> "$D/logs/s1-meta.log" 2>&1 && stamp s1 meta ok
  $R -R --exclude="/$V/raw/laz/pistepilvi/" "$V" "$H:$F/" > "$D/logs/s1-version.log" 2>&1 && stamp s1 version ok
  $R --files-from="$D/version-only-laz.txt" "$V/raw/laz/pistepilvi/" "$H:$F/$V/raw/laz/pistepilvi/" > "$D/logs/s1-vlaz.log" 2>&1 &&
    stamp s1 version-only-laz ok
  $R -R "${PRODUCTS[@]}" "$H:$F/" > "$D/logs/s1-products.log" 2>&1 && stamp s1 products ok
  cd /home/wnt || return
  $R -R turku3d-bulk-warmup/envs turku3d-bulk-warmup/tools turku3d-local/loops/tiles/shared-tiles-run.sh "$H:/home/wnt/" \
    > "$D/logs/s1-env.log" 2>&1 && stamp s1 env ok
  stamp "s1 done"
}
sl() { stamp "laz-$1 start"; $R --files-from="$D/supply-part.$1" "$SUP/" "$H:$SUP/" > "$D/logs/laz-$1.log" 2>&1 && stamp "laz-$1 ok"; stamp "laz-$1 done"; }
rm -f "$D"/logs/*.log
s1 & for p in supply-part.*; do sl "${p##*.}" & done; wait
# The version's blocks that lidarpc also has: hard links on the host (same bytes; verify checks the catalog SHA-256).
comm -12 version-laz.txt supply.txt | ssh -i "$HOME/.ssh/bulk_gen3" "$H" "cd $F/$V/raw/laz/pistepilvi && n=0
  while read -r b; do [ -e \"\$b\" ] || { ln \"$SUP/\$b\" \"\$b\" && n=\$((n+1)); }; done; echo linked \$n" > "$D/logs/link.log" 2>&1 &&
  stamp "version laz $(tail -1 "$D/logs/link.log")"
if grep -l "rsync error" "$D"/logs/*.log > /dev/null; then stamp "all done WITH ERRORS: $(grep -l 'rsync error' "$D"/logs/*.log | xargs)"
else stamp "all done"; fi
