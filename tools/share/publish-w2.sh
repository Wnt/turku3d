#!/usr/bin/env bash
# Publishes the new world (W2) to https://city.lab.madekivi.fi and nowhere else.
#
#   tools/share/publish-w2.sh [--rev REV] [--store HOST_DIR]... [--no-client | --no-stores]
#
# The lab's only machine is osgallery-dev: every ssh and rsync of this script goes to the constant SSH_HOST below, and
# the only units it touches are that host's user unit `city-web` (and only when its unit file changed). It never
# talks to OCI, city.madekivi.fi (frozen) or Pages, and takes no host from the environment. tools/share/deploy.sh
# is the full share deploy of the old game and is not used for the new world.
#
# What one run does:
#  1. Client: builds REV (default local/loop-integration) on this Mac in a detached worktree with the share-host
#     settings of the lab (VITE_SHARE_HOSTS, VITE_PUBLIC_RELEASE) and uploads the dist to
#     ~/turku3d-local/city/releases/w2-<sha>/dist (files the running release already has are hard-linked, not sent).
#     A sha that is already built is not built again; --no-client skips the client altogether.
#  2. Locations: the world stores that location builds left ON THE HOST (default: this track's
#     ~/turku3d-w2/w2-play/work/world; one --store per further directory, relative to the host's home) are published
#     into ~/turku3d-local/city/world: objects by hard link (they are named by their content), every location
#     manifest whose file is newer, and index.json, the list the start page shows. Nothing is uploaded from the Mac.
#     --no-stores skips this.
#  3. The first run keeps the release that was live before the new world as ~/turku3d-local/city/classic (hard
#     links), served at /classic/: the old next-generation build stays reachable.
#  4. Installs tools/share/units/city-web.service of REV when it differs from the installed one (the /world and
#     /classic mounts) and restarts city-web then; switches `current`; checks the public URL.
#
# city-web serves current/dist per request, so a republish needs no restart. Build a location first, for example
#   scripts/dev/on-host osg --data world/descriptions --data raw/osm --no-merge -- python -m turku3d.world build --area portsa
set -euo pipefail

SSH_HOST=osgallery-dev
PUBLIC_HOST=city.lab.madekivi.fi
REPO=$HOME/turku3d/wt/loop-integration/repo
WORK=$HOME/turku3d/loops/w2/play
C=turku3d-local/city
REV=local/loop-integration
STORES=()
CLIENT=1
PUBLISH_STORES=1
while [ $# -gt 0 ]; do
  case "$1" in
    --rev) REV=$2; shift 2 ;;
    --store) STORES+=("$2"); shift 2 ;;
    --no-client) CLIENT=0; shift ;;
    --no-stores) PUBLISH_STORES=0; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ ${#STORES[@]} -gt 0 ] || STORES=(turku3d-w2/w2-play/work/world)
for store in "${STORES[@]}"; do
  [[ "$store" =~ ^[A-Za-z0-9._/-]+$ && "$store" != /* && "$store" != *..* ]] || { echo "bad --store (a path under the host's home): $store" >&2; exit 2; }
done
[ "$SSH_HOST" = osgallery-dev ] && [ "$PUBLIC_HOST" = city.lab.madekivi.fi ] || { echo 'this script publishes to the lab only' >&2; exit 2; }
remote() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$SSH_HOST" "$@"; }

SHA=$(git -C "$REPO" rev-parse --verify "$REV^{commit}")
SHORT=${SHA:0:8}
SUBJECT=$(git -C "$REPO" log -1 --format=%s "$SHA")
say() { printf 'publish-w2 %s: %s\n' "$SHORT" "$*"; }
PHASE=$(date +%s)
phase() { local now; now=$(date +%s); say "$1: $((now - PHASE)) s"; PHASE=$now; }
mkdir -p "$WORK/builds" "$WORK/stage"

if [ "$CLIENT" = 1 ]; then
  BUILD=$WORK/builds/$SHA
  if [ ! -f "$BUILD/dist/index.html" ]; then
    WT=$WORK/stage/$SHA
    git -C "$REPO" worktree prune
    [ -d "$WT" ] && git -C "$REPO" worktree remove --force "$WT"
    git -C "$REPO" worktree add -q --detach "$WT" "$SHA"
    ln -s "$(cd "$REPO/game/node_modules" && pwd -P)" "$WT/game/node_modules"
    say "building the client on the Mac (share host $PUBLIC_HOST)"
    (cd "$WT/game" && TURKU3D_BUILD_COMMIT="$SHA" TURKU3D_WORK="$BUILD/work" VITE_SHARE_HOSTS=$PUBLIC_HOST VITE_PUBLIC_RELEASE=1 \
      ./node_modules/.bin/vite build --logLevel warn --outDir "$BUILD/dist")
    git -C "$REPO" worktree remove --force "$WT"
    phase 'built the client'
  fi
  python3 - "$BUILD/dist/version.json" "$SHA" "$SUBJECT" <<'PY'
import json, sys, time
path, sha, subject = sys.argv[1:]
json.dump(dict(sha=sha, subject=subject, scenes=sha, world='w2',
               built=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())), open(path, 'w'), indent=1)
PY
  git -C "$REPO" show "$SHA:tools/share/units/city-web.service" > "$BUILD/city-web.service"
  remote "mkdir -p ~/$C/releases/w2-$SHA/dist"
  say "uploading $(du -sh "$BUILD/dist" | cut -f1) dist to $SSH_HOST (unchanged files are linked from the live release)"
  rsync -a --delete --exclude '*.map' --exclude '*.br' --link-dest="../../../current/dist/" "$BUILD/dist/" "$SSH_HOST:$C/releases/w2-$SHA/dist/"
  rsync -a "$BUILD/city-web.service" "$SSH_HOST:$C/releases/w2-$SHA/city-web.service"
  phase 'uploaded the client'
fi

say "publishing on $SSH_HOST"
remote "flock -w 900 ~/$C/deploy.lock bash -s -- $SHA $CLIENT $PUBLISH_STORES ${STORES[*]}" <<'REMOTE'
set -euo pipefail
SHA=$1 CLIENT=$2 PUBLISH_STORES=$3; shift 3
C=$HOME/turku3d-local/city U=$HOME/.config/systemd/user R=$C/releases/w2-$SHA W=$C/world
mkdir -p "$W/locations" "$W/objects"
if [ "$PUBLISH_STORES" = 1 ]; then
  for store in "$@"; do
    S=$HOME/$store
    [ -d "$S/locations" ] || { echo "no locations in $S (nothing built there yet)"; continue; }
    # Objects are named by their content: a hard link per new file, nothing copied or overwritten.
    rsync -a --ignore-existing --link-dest="$S/objects/" "$S/objects/" "$W/objects/"
    # A manifest is replaced by rename on its build host, so every file read here is whole; the newer one wins.
    rsync -a --update --include '*.json' --exclude '*' "$S/locations/" "$W/locations/"
  done
  python3 - "$W" <<'PY'
import json, os, sys, time
root = sys.argv[1]
rows = []
for name in sorted(os.listdir(f'{root}/locations')):
    if not name.endswith('.json'):
        continue
    try:
        m = json.load(open(f'{root}/locations/{name}'))
    except ValueError:
        continue
    missing = sum(1 for e in m['entities'] if e.get('url') and not os.path.exists(f"{root}/{e['url']}"))
    rows.append(dict(name=m['name'], revision=m['revision'], built=m['built'], query=m['query'], focus=m['focus'],
                     entities=m['stats']['entities'], triangles=m['stats']['triangles'], bytes=m['stats']['bytes'], missing=missing))
    print(f"location {m['name']} r{m['revision']} built {m['built']}: {m['stats']['entities']} entities, {missing} missing objects")
tmp = f'{root}/index.json.tmp'
json.dump(dict(schema='turku-world-index/1', published=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), locations=rows), open(tmp, 'w'), indent=1)
os.replace(tmp, f'{root}/index.json')
PY
fi
if [ "$CLIENT" = 1 ]; then
  # The release that was live before the new world stays reachable at /classic/ (hard links; made once).
  CUR=$(readlink "$C/current" || true)
  if [ ! -d "$C/classic/dist" ] && [ -n "$CUR" ] && [[ "$CUR" != releases/w2-* ]] && [ -d "$C/$CUR/dist" ]; then
    mkdir -p "$C/classic" && cp -al "$C/$CUR/dist" "$C/classic/dist"
    echo "kept $CUR as classic"
  fi
  mkdir -p "$C/classic/dist"
  # Brotli sidecars for the big compressible files, as the share deploy makes them.
  nice -n 10 node "$C/web/src/tools/share/precompress.mjs" "$R/dist"
  ln -sfn "releases/w2-$SHA" "$C/current.new" && mv -T "$C/current.new" "$C/current"
  if ! cmp -s "$R/city-web.service" "$U/city-web.service"; then
    cp "$R/city-web.service" "$U/city-web.service"
    systemctl --user daemon-reload
    systemctl --user restart city-web
    echo 'city-web unit changed: restarted'
  fi
  # Keep the three newest W2 releases; never the live one, never a release of the share deploy.
  keep=$(readlink "$C/current")
  ls -1dt "$C"/releases/w2-*/ 2>/dev/null | tail -n +4 | while read -r old; do
    name=$(basename "$old")
    if [[ "$name" =~ ^w2-[a-f0-9]{40}$ ]] && [ "releases/$name" != "$keep" ]; then rm -rf "$C/releases/$name"; fi
  done
  echo "current: $(readlink "$C/current")"
fi
REMOTE
phase 'published'

if [ "$CLIENT" = 1 ]; then
  live=''
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    live=$(curl -fsS -m 5 "https://$PUBLIC_HOST/version.json" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["sha"])' 2>/dev/null || true)
    [ "$live" = "$SHA" ] && break
    sleep 2
  done
  [ "$live" = "$SHA" ] || { say "FAILED: https://$PUBLIC_HOST/version.json serves '${live:-nothing}'"; exit 1; }
fi
curl -fsS -m 10 "https://$PUBLIC_HOST/world/index.json" | python3 -c '
import json, sys
index = json.load(sys.stdin)
for row in index["locations"]:
    print("  https://city.lab.madekivi.fi/?world=%s   r%s, built %s, %s entities%s" % (row["name"], row["revision"], row["built"], row["entities"], ", %d OBJECTS MISSING" % row["missing"] if row["missing"] else ""))
'
phase 'verified the public site'
say "live at https://$PUBLIC_HOST/"
