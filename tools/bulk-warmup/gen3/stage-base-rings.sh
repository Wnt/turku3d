#!/bin/bash
# Base products to the bulk host ring by ring (rsync sorts one file list by name; one rsync per ring keeps the order).
F=/home/wnt/turku3d-local/loops/tiles/farm; D=$HOME/turku3d-local/loops/bulkgen3
export RSYNC_RSH="ssh -i $HOME/.ssh/bulk_gen3 -c aes128-gcm@openssh.com"
cd $F || exit 1
python3 - $D/base-by-ring.txt > $D/base-rings.txt <<PY
import sys
r = lambda s: max(abs(int(v)) for v in s.rsplit("/", 1)[1].split("_")[1:])
rows = [l.strip() for l in open(sys.argv[1]) if l.strip()]
for k in range(0, 21):
    print(" ".join(x for x in rows if r(x) == k))
print(" ".join(x for x in rows if r(x) > 20))
PY
n=0; while read -r dirs; do
  nice -n5 rsync -aHR --numeric-ids --partial-dir=.rsync-partial $dirs root@10.77.0.2:$F/ && echo "$(date -u +%FT%TZ) base ring $n ok" >> $D/stage-local.log
  n=$((n+1))
done < $D/base-rings.txt
echo "$(date -u +%FT%TZ) base all rings ok" >> $D/stage-local.log
