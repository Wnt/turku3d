#!/bin/bash
# osgallery-dev: full staging of a window's inputs onto the bulk host's local disk (the build path reads no NFS).
#   stage-local.sh ALIAS [RADIUS]    (default radius 16 rings round t0_0_0; everything over the WireGuard tunnel,
#                                     so its 480 Mbit/s cap keeps the home uplink for city.lab; resumable)
# The host's farm and /bulk are plain local trees at their osgallery-dev paths (made from the cached host's seeds and
# upper layers by hard links). This adds what the queued tiles read:
#   - farm metadata, every revision.json, the code trees of the alias and its spot-rebuild revisions;
#   - the product generations resolve.py names (base, steps, checkpoint, spot rebuilds);
#   - the farm's shared stage caches except raw-derived (rebuilds recompute those per block);
#   - Turku survey blocks of the tiles within RADIUS (the version's own and lidarpc's), copied on the host by
#     window-warm.py with the catalog size + SHA-256 check, reading the NFS mount as its source;
#   - the cities' raw trees (host-side rsync from the mount; the warm FS-Cache serves what it already holds).
# Logs to ~/turku3d-local/loops/bulkgen3/stage-local.log.
set -uo pipefail
A=${1:?alias}; RADIUS=${2:-16}
HERE=$(cd "$(dirname "$0")" && pwd)
D=$HOME/turku3d-local/loops/bulkgen3
F=/home/wnt/turku3d-local/loops/tiles/farm
H=root@10.77.0.2
export RSYNC_RSH="ssh -i $HOME/.ssh/bulk_gen3 -c aes128-gcm@openssh.com -o ServerAliveInterval=30"
R="nice -n19 ionice -c3 rsync -aH --numeric-ids --partial-dir=.rsync-partial"
log() { echo "$(date -u +%FT%TZ) $*" >> "$D/stage-local.log"; }
python3 "$HERE/resolve.py" --farm-root $F "$A" > "$D/resolved-$A.json" || exit 1
REV=$(python3 -c "import json; print(json.load(open('$D/resolved-$A.json'))['aliases']['$A']['revision'])")
mapfile -t GENS < <(python3 -c "import json; print('\n'.join(sorted(set(json.load(open('$D/resolved-$A.json'))['products']))))")
mapfile -t REVS < <(cd $F && python3 - "$REV" <<'PY'
import json, sys
from pathlib import Path
rev = sys.argv[1]; meta = json.loads(Path(f'revisions/{rev}/revision.json').read_text())
revs = {rev, meta['code_revision'], meta['base_revision']}
revs |= {s['rebuild_revision'] for s in meta['migrations'] if s.get('rebuild_revision')}
print('\n'.join(f'revisions/{r}/' for r in sorted(revs) if Path(f'revisions/{r}').is_dir()))
PY
)
cd $F || exit 1
log "start $A $REV radius $RADIUS: ${#GENS[@]} generations, ${#REVS[@]} revisions"
(
  $R -R aliases.json pin.json fallbacks.json "${REVS[@]}" "$H:$F/" &&
  $R -R --include='/revisions/' --include='/revisions/*/' --include='/revisions/*/*.json' --exclude='*' ./ "$H:$F/" &&
  log "meta ok"
) &
( $R -R "${GENS[@]/#/products/}" "$H:$F/" && log "products ok" ) &
( $R -R --exclude='/cache/raw-derived/' cache "$H:$F/" && log "cache ok" ) &
wait
ssh -i "$HOME/.ssh/bulk_gen3" $H "chown -R 1000:1000 $F"
# Survey blocks (host side, verified) and the cities' raw trees, in parallel.
ssh -i "$HOME/.ssh/bulk_gen3" $H "G=/home/wnt/turku3d-bulk-gen3; V=\$(python3 -c \"import json; m=json.load(open('$F/revisions/$REV/revision.json')); print(json.load(open('$F/revisions/'+m['base_revision']+'/revision.json'))['data_root'])\")
  C=\$(python3 -c \"import json; print(json.load(open('$F/revisions/$REV/revision.json'))['code_revision'])\")
  ( su wnt -c \"cd $F/revisions/\$C/repo && env REPO=$F/revisions/\$C/repo TURKU3D_WORK=$F/work/migrate \
      /home/wnt/turku3d-local/loops/tiles/shared-tiles-run.sh python \$G/window-warm.py --farm-root $F --revision $REV \
      --radius $RADIUS --ahead 1000000 --threads 4 --supply /srv/osg/farm/\${V#$F/}/raw/laz/pistepilvi \
      --supply /srv/osg/bulk/lidarpc/raw/pistepilvi\" > \$G/logs/stage-blocks.log 2>&1; echo blocks \$? ) &
  ( rsync -a /srv/osg/bulk/cities/ /bulk/turku3d/cities/ && chown -R 1000:1000 /bulk/turku3d/cities; echo cities \$? ) &
  wait; tail -1 \$G/logs/stage-blocks.log" >> "$D/stage-local.log" 2>&1
log "done"
