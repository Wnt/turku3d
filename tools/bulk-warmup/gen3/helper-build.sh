#!/bin/bash
# Mac: build tiles first+full on the UpCloud build helper (the cached bulk host) instead of the loaded osgallery-dev farm.
#   helper-build.sh TILE [TILE ...] --rev REV_OR_ALIAS     a revision registered on the farm (64 hex, or an alias such
#                   as train5): the helper's farm service builds it exactly as the farm would, and each finished tile
#                   is copied into osgallery-dev's farm store (products/<generation>/TILE, ready.json last), so the
#                   farm, its follow chain and city.lab serve it at once. Already-built tiles return immediately.
#   helper-build.sh TILE [TILE ...] --commit GITREV [--out DIR]   any commit of this repo, unregistered: the code tree
#                   goes to the helper, the tile builds with the farm's environment (the train base's source version,
#                   the shared stage caches), and the result comes back to DIR/<sha>/TILE (default
#                   ~/turku3d/helper-builds). Nothing is written to the farm.
# Prints one JSON line per tile (state, wall seconds, generation or path). Builds run in parallel, 4 at once on the
# helper. A stopped helper is started first (about 1 min); it powers itself off 55 min after its last build
# (helper-ttl.sh), with osgallery-dev's helper-guard.py as backstop. The host is the one in ~/turku3d/bulk-cache/server.json.
set -euo pipefail
# Commits are archived from this checkout (any repo that has them; default the integration worktree).
REPO=${HELPER_REPO:-$HOME/turku3d/wt/loop-integration/repo}
G=${BULK_STATE:-$HOME/turku3d/bulk-cache}
OSG=osgallery-dev
F=/home/wnt/turku3d-local/loops/tiles/farm
API=https://api.upcloud.com/1.3
TILES=(); REV=; COMMIT=; OUT=$HOME/turku3d/helper-builds
while [ $# -gt 0 ]; do
  case "$1" in
    --rev) REV=$2; shift 2 ;;
    --commit) COMMIT=$2; shift 2 ;;
    --out) OUT=$2; shift 2 ;;
    t0_*) TILES+=("$1"); shift ;;
    *) sed -n 2,14p "$0"; exit 2 ;;
  esac
done
[ ${#TILES[@]} -ge 1 ] && { [ -n "$REV" ] || [ -n "$COMMIT" ]; } || { sed -n 2,14p "$0"; exit 2; }
conf() { python3 -c "import json; print(json.load(open('$G/server.json'))['$1'])"; }
IP=$(conf ip); UUID=$(conf uuid); H=root@$IP
api() { curl -sf -H "Authorization: Bearer $(cat ~/.config/up_token)" -H 'Content-Type: application/json' "$@"; }
state() { api "$API/server/$UUID" | python3 -c 'import json,sys; print(json.load(sys.stdin)["server"]["state"])'; }

# 1. The helper is on, its farm tree is there, and it stays on while we wait.
s=$(state)
if [ "$s" != started ]; then
  until [ "$s" = stopped ] || [ "$s" = started ]; do sleep 5; s=$(state); done
  [ "$s" = stopped ] && api -X POST "$API/server/$UUID/start" > /dev/null
  until [ "$(state)" = started ]; do sleep 5; done
fi
until ssh -o ConnectTimeout=5 -o BatchMode=yes "$H" "test -d $F/products" 2>/dev/null; do sleep 5; done
ssh "$H" /home/wnt/turku3d-bulk-gen3/helper-ttl.sh extend

pull() {  # pull GEN TILE: one finished farm tile into osgallery-dev's store, its files first, ready.json last
  ssh $OSG "cd $F && [ -f products/$1/$2/ready.json ] && exit 0
    export RSYNC_RSH='ssh -i \$HOME/.ssh/bulk_gen3 -c aes128-gcm@openssh.com'
    R='nice -n19 rsync -aH --numeric-ids --no-D'; mkdir -p products/$1
    \$R --exclude=ready.json root@10.77.0.2:/srv/farm/upper/products/$1/$2/ products/$1/$2/ &&
    \$R root@10.77.0.2:/srv/farm/upper/products/$1/$2/ready.json products/$1/$2/ready.json"
}

if [ -n "$REV" ]; then
  REV=$(ssh $OSG "python3 -c \"import json; print(json.load(open('$F/aliases.json')).get('$REV', '$REV'))\"")
  # The revision's code trees as local copies in the helper's overlay (an NFS open per file is a round trip; the
  # NFS attribute cache can also hide a just-registered revision for up to a minute).
  ssh $OSG "cd $F && python3 - $REV" <<'PY' > "$G/helper-paths.txt"
import json, sys
from pathlib import Path
rev = sys.argv[1]; meta = json.loads(Path(f'revisions/{rev}/revision.json').read_text())
revs = {rev, meta.get('code_revision', rev)} | {s['rebuild_revision'] for s in meta.get('migrations', []) if s.get('rebuild_revision')}
print('\n'.join(f'revisions/{r}/' for r in sorted(revs) if Path(f'revisions/{r}').is_dir()))
PY
  ssh $OSG "cd $F && nice -n19 rsync -aR --numeric-ids -e 'ssh -i /home/wnt/.ssh/bulk_gen3 -c aes128-gcm@openssh.com' \
            $(tr '\n' ' ' < "$G/helper-paths.txt") root@10.77.0.2:$F/" > /dev/null
  ssh "$H" "chown -R 1000:1000 /srv/farm/upper/revisions 2>/dev/null; true"
  for t in "${TILES[@]}"; do
    (
      row=$(ssh "$H" "/home/wnt/turku3d-bulk-gen3/helper-run.sh farm $REV $t" || true)
      gen=$(python3 -c "import json,sys; r=json.loads(sys.argv[1] or '{}'); print(r.get('generation', ''))" "$row" 2>/dev/null || true)
      if [ -n "$gen" ] && pull "$gen" "$t"; then
        python3 -c "import json,sys; r=json.loads(sys.argv[1]); r['farm']='osgallery-dev'; print(json.dumps(r))" "$row"
      else echo "${row:-{\"tile\": \"$t\", \"state\": \"failed\"\}}"; fi
    ) &
  done
  wait
else
  SHA=$(git -C "$REPO" rev-parse "$COMMIT^{commit}")
  ssh "$H" "test -d /home/wnt/turku3d-bulk-gen3/commits/$SHA" ||
    git -C "$REPO" archive --format=tar "$SHA" | ssh "$H" "mkdir -p /home/wnt/turku3d-bulk-gen3/commits/$SHA.tmp &&
      tar xf - -C /home/wnt/turku3d-bulk-gen3/commits/$SHA.tmp && mv /home/wnt/turku3d-bulk-gen3/commits/$SHA.tmp \
      /home/wnt/turku3d-bulk-gen3/commits/$SHA && chown -R 1000:1000 /home/wnt/turku3d-bulk-gen3/commits"
  mkdir -p "$OUT/$SHA"
  for t in "${TILES[@]}"; do
    (
      row=$(ssh "$H" "/home/wnt/turku3d-bulk-gen3/helper-run.sh commit $SHA $t" || true)
      if [[ $row == *'"built"'* ]] && rsync -a "$H:/home/wnt/turku3d-bulk-gen3/out/$SHA/$t/" "$OUT/$SHA/$t/"; then
        python3 -c "import json,sys; r=json.loads(sys.argv[1]); r['path']=sys.argv[2]; print(json.dumps(r))" "$row" "$OUT/$SHA/$t"
      else echo "${row:-{\"tile\": \"$t\", \"state\": \"failed\"\}}"; fi
    ) &
  done
  wait
fi
