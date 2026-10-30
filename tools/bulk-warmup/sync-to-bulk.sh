#!/usr/bin/env bash
# Run on OCI as ubuntu: copy the generator inputs and caches straight to the bulk host, several
# parallel streams over SSH (aes128-gcm). BULK_HOST=root@<ip>, key ~/.ssh/bulk_upcloud.
# The OCI data volume (~80 MB/s) is the limit: keep the stream count low and pause the OCI warm-up
# (hold-prebuild.sh) while this runs. Stage caches are optional (the bulk host rebuilds them fast).
set -uo pipefail
R="${BULK_HOST:?BULK_HOST=root@ip}:/root/turku3d-bulk-warmup"
E="ssh -c aes128-gcm@openssh.com -i $HOME/.ssh/bulk_upcloud -o IdentitiesOnly=yes"
S=/srv/turku3d
run() { name=$1; shift; ( s=$(date +%s); rsync -a --info=stats1 -e "$E" "$@" > ~/bulk-sync-$name.log 2>&1; echo "exit=$? seconds=$(( $(date +%s)-s ))" >> ~/bulk-sync-$name.log ) & }
run raw        $S/data/raw/ $R/data/raw/
run data-other --exclude=raw --exclude=cache/streetlevel $S/data/ $R/data/
run lidar      $S/fast-cache/cache/lidar/ $R/work/cache/lidar/
run cache-rest $S/fast-cache/cache/coarse-roofs $S/fast-cache/cache/coarse-area $S/fast-cache/cache/inputs $S/work/cache/vegetation $R/work/cache/
run inputs     $S/fast-cache/coarse/.inputs/ $R/work/coarse/.inputs/
run work-small $S/work/coarse/shared $S/work/coarse/navigation-*.json $R/work/coarse/
wait
rsync -a -e "$E" $S/work/facade-rules-models $S/work/prebuild-tiles.json $R/work/
target=$(readlink $S/work/facade-rules.json)
$E "${BULK_HOST}" "ln -sfn /root/turku3d-bulk-warmup/work/facade-rules-models/$(basename "$target") /root/turku3d-bulk-warmup/work/facade-rules.json"
if [ "${WITH_STAGES:-0}" = 1 ]; then
  rsync -a -e "$E" $S/fast-cache/coarse/.stages/ $R/work/coarse/.stages/
fi
echo ALLDONE > ~/bulk-sync-done
