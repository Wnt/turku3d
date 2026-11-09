#!/usr/bin/env bash
# Public share of the local next-gen game at https://city.lab.madekivi.fi, served from osgallery-dev without the Mac.
#
#   tools/share/deploy.sh [--local-build] [REV]    # default: local/loop-integration of ~/turku3d/wt/loop-integration/repo
#
# Default: streams a `git archive` of REV (game, tools/dev, tools/share, server, the tile proxy: ~70 MB) to osgallery-dev
# and builds it THERE (nice 10, deps cached by game/package-lock.json hash in ~/turku3d-local/city/deps/<hash>), so a
# deploy costs the Mac only the archive and a few ssh calls. The build, release install and unit restarts run under
# one flock on osgallery-dev (~/turku3d-local/city/deploy.lock), so concurrent deploys queue instead of colliding.
# --local-build: build on this Mac instead (when osgallery-dev cannot build) and rsync the dist; same publish step.
#
# The build uses VITE_SHARE_HOSTS=city.lab.madekivi.fi (same-origin tiles, point cloud and multiplayer;
# game/src/core/localOnly.ts) and VITE_PUBLIC_RELEASE=1 (the Pages start menu, no capture; ?editor=1 keeps edits in the browser), copies the dist
# and the server sources to osgallery-dev ~/turku3d-local/city/releases/<sha>, pins the tile proxy to the local game's
# tile version (~/turku3d/loops/integrator/tile-service.json), switches `current`, installs the units, restarts what
# changed and checks the public URL. Big compressible dist files get brotli sidecars city-web serves as they are
# (tools/share/precompress.mjs). /version.json names the deployed commit. Content-hashed assets of the builds
# deployed in the last 12 h stay served next to the new ones, so a page that loaded an earlier build keeps working.
#
# Tiles resolve per tile (scripts/dev/shared-tile-proxy.mjs `follow`): newest first through the farm's train<N>
# aliases on the pin's base generation, then the pin. A tile comes from the newest train that finished it; tiles a city
# pass finishes later, and new trains, show up on their own with no redeploy.
#
# systemd --user units on osgallery-dev (linger; Restart=always; enabled for boot), sources in tools/share/units:
#   city-web        tools/dev/serve-dist.mjs --public, 127.0.0.1:7780: the game, /pointcloud -> /bulk/turku3d/lidarpc/copc,
#                   /coarse -> city-tiles, /cities/<name>/coarse -> that city's tile service as named in
#                   ~/turku3d-local/city/cities.json ({"espoo": "http://127.0.0.1:7970"}, re-read on change: adding a
#                   line opens that city's portals without a redeploy), WebSocket + /live.json + /scores/ -> city-mp;
#                   GET/HEAD only
#   city-tiles      scripts/dev/shared-tile-proxy.mjs, 127.0.0.1:7781 -> farm 192.168.1.114:7950 (/v/<pin>) and the
#                   scenery server 127.0.0.1:7947; pin in ~/turku3d-local/city/tiles.json (hot-read)
#   city-mp         server/index.js, 127.0.0.1:7782, TURKU_MP_ORIGINS=https://city.lab.madekivi.fi, own scores dir;
#                   restarted only when server/ changes (players reconnect with their session)
#   city-forwarder  forwarder-agent --tunnel city:http:city.lab.madekivi.fi:7780 (token: ~/.config/turku3d/forwarder.env)
# city-web and city-tiles listen on city-web.socket / city-tiles.socket (socket activation, tools/dev/systemdListen.mjs):
# a restart drains the requests in flight and new ones wait in the held socket, so a deploy answers nobody 502.
# The farm, scenery and COPC services the share reads run as shared-tiles, local-horizon and lidar-copc user units.
#
# Env overrides: TURKU3D_SHARE_REPO, TURKU3D_SHARE_SSH (osgallery-dev), TURKU3D_SHARE_PIN (a tile-service.json),
# TURKU3D_SHARE_SCENERY (http://127.0.0.1:7947), TURKU3D_SHARE_WORK (Mac build dir, --local-build only), TURKU3D_SHARE_FOLLOW (alias
# family followed per tile, default train; empty for the pin only), TURKU3D_SHARE_EXCLUDE (space-separated aliases of
# that family never served, e.g. a train Jonni has not accepted).
set -euo pipefail

REPO=${TURKU3D_SHARE_REPO:-$HOME/turku3d/wt/loop-integration/repo}
SSH_HOST=${TURKU3D_SHARE_SSH:-osgallery-dev}
PIN=${TURKU3D_SHARE_PIN:-$HOME/turku3d/loops/integrator/tile-service.json}
SCENERY=${TURKU3D_SHARE_SCENERY:-http://127.0.0.1:7947}
WORK=${TURKU3D_SHARE_WORK:-$HOME/turku3d/loops/cityshare/work}
FOLLOW=${TURKU3D_SHARE_FOLLOW-train}
EXCLUDE=${TURKU3D_SHARE_EXCLUDE:-}
PUBLIC_HOST=city.lab.madekivi.fi
LOCAL_BUILD=0
if [ "${1:-}" = "--local-build" ]; then LOCAL_BUILD=1; shift; fi
REV=${1:-local/loop-integration}
KEEP=3

SHA=$(git -C "$REPO" rev-parse --verify "$REV^{commit}")
SHORT=${SHA:0:8}
SERVER_TREE=$(git -C "$REPO" rev-parse "$SHA:server")
# The code city-web and city-tiles run: a deploy that leaves it unchanged does not restart them (see below).
WEB_TREE=$(git -C "$REPO" rev-parse "$SHA:tools/dev")-$(git -C "$REPO" rev-parse "$SHA:scripts/dev/shared-tile-proxy.mjs")
say() { printf 'deploy %s: %s\n' "$SHORT" "$*"; }

VERSION=$(python3 - "$PIN" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))['version'])
PY
)
SUBJECT=$(git -C "$REPO" log -1 --format=%s "$SHA")
META=$(mktemp -d)
trap 'rm -rf "$META"' EXIT
python3 - "$META/version.json" "$SHA" "$SUBJECT" "$VERSION" "$FOLLOW" "$EXCLUDE" <<'PY'
import json, sys, time
path, sha, subject, tiles, follow, exclude = sys.argv[1:]
json.dump(dict(sha=sha, subject=subject, tiles=tiles, follow=follow, exclude=exclude.split(),
               built=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())), open(path, 'w'), indent=1)
PY
# Tile pin (hot-read by city-tiles): the local game's version on the same farm, the alias family followed per
# tile (newest finished compatible train first), scenery from osgallery-dev itself.
python3 - "$PIN" "$SCENERY" "$FOLLOW" "$EXCLUDE" > "$META/tiles.json" <<'PY'
import json, sys
pin = json.load(open(sys.argv[1]))
print(json.dumps(dict(service=pin['service'], version=pin['version'], scenery=sys.argv[2],
                      **(dict(follow=sys.argv[3], exclude=sys.argv[4].split()) if sys.argv[3] else {}))))
PY

# 1. Ship the committed tree (plus version.json and tiles.json at its root) to a stage dir on osgallery-dev.
C=turku3d-local/city
STAGE="$C/stage/$SHORT-$$"
ssh "$SSH_HOST" "mkdir -p ~/$C/stage ~/$C/releases ~/$C/mp-scores ~/$C/work ~/$C/deps && rm -rf ~/$STAGE && mkdir -p ~/$STAGE
  [ -f ~/$C/cities.json ] || echo '{}' > ~/$C/cities.json"
git -C "$REPO" archive --add-file="$META/version.json" --add-file="$META/tiles.json" "$SHA" \
  game tools/dev tools/share scripts/dev/shared-tile-proxy.mjs server pipeline/turku3d/night_lighting.json | ssh "$SSH_HOST" "tar -x -C ~/$STAGE"

if [ "$LOCAL_BUILD" = 1 ]; then
  # Mac build in a detached worktree (vite.config.ts stamps the commit from git), dist uploaded into the stage.
  BUILD="$WORK/builds/$SHA"
  if [ ! -f "$BUILD/game/index.html" ]; then
    WT="$WORK/stage/$SHA"
    rm -rf "$WT" "$BUILD"
    git -C "$REPO" worktree prune
    git -C "$REPO" worktree add -q --detach "$WT" "$SHA"
    trap 'git -C "$REPO" worktree remove --force "$WT" >/dev/null 2>&1 || true; rm -rf "$META"' EXIT
    ln -s "$(cd "$REPO/game/node_modules" && pwd -P)" "$WT/game/node_modules"
    say "building on the Mac (vite, share host $PUBLIC_HOST)"
    (cd "$WT/game" && TURKU3D_WORK="$BUILD/work" VITE_SHARE_HOSTS=$PUBLIC_HOST VITE_PUBLIC_RELEASE=1 \
      ./node_modules/.bin/vite build --logLevel warn --outDir "$BUILD/game")
    git -C "$REPO" worktree remove --force "$WT"
    trap 'rm -rf "$META"' EXIT
  fi
  cp "$META/version.json" "$BUILD/game/version.json"
  say "uploading $(du -sh "$BUILD/game" | cut -f1) dist"
  rsync -a --delete --exclude '*.map' "$BUILD/game/" "$SSH_HOST:$STAGE/dist/"
  find "$WORK/builds" -mindepth 1 -maxdepth 1 -type d ! -name "$SHA" -mtime +2 -exec rm -rf {} + 2>/dev/null || true
fi

# 2. On osgallery-dev, under the deploy lock: build (unless --local-build), install the release, switch, restart, prune.
say "publishing from osgallery-dev$([ "$LOCAL_BUILD" = 1 ] || echo ' (remote build)')"
ssh "$SSH_HOST" "flock -w 1800 ~/$C/deploy.lock bash -s -- $SHA $SERVER_TREE $STAGE $LOCAL_BUILD $PUBLIC_HOST $KEEP $WEB_TREE" <<'REMOTE'
set -euo pipefail
SHA=$1 SERVER_TREE=$2 STAGE=$HOME/$3 LOCAL_BUILD=$4 PUBLIC_HOST=$5 KEEP=$6 WEB_TREE=$7 ASSET_HOURS=12
C=$HOME/turku3d-local/city U=$HOME/.config/systemd/user R=$C/releases/$SHA
trap 'rm -rf "$STAGE"' EXIT
if [ "$LOCAL_BUILD" = 0 ]; then
  # Dependencies cached by lockfile hash; npm ci runs once per distinct lockfile.
  LOCK=$(sha256sum < "$STAGE/game/package-lock.json" | cut -c1-16)
  D=$C/deps/$LOCK
  if [ ! -f "$D/.ok" ]; then
    rm -rf "$D"; mkdir -p "$D"
    cp "$STAGE/game/package.json" "$STAGE/game/package-lock.json" "$D/"
    echo "installing game deps ($LOCK)"
    (cd "$D" && npm ci --ignore-scripts --no-audit --no-fund --loglevel=error)
    touch "$D/.ok"
  fi
  ln -s "$D/node_modules" "$STAGE/game/node_modules"
  # The archive has no Git objects; stamp its source revision without manufacturing an incomplete repository.
  echo "building (vite, share host $PUBLIC_HOST)"
  (cd "$STAGE/game" && TURKU3D_BUILD_COMMIT="$SHA" TURKU3D_WORK="$STAGE/work" VITE_SHARE_HOSTS=$PUBLIC_HOST VITE_PUBLIC_RELEASE=1 \
    nice -n 10 ./node_modules/.bin/vite build --logLevel warn --outDir "$STAGE/dist")
  find "$STAGE/dist" -name '*.map' -delete
  cp "$STAGE/version.json" "$STAGE/dist/version.json"
fi
# Brotli sidecars for the big compressible files (tools/share/precompress.mjs): city-web never compresses them at runtime.
nice -n 10 node "$STAGE/tools/share/precompress.mjs" "$STAGE/dist"
mkdir -p "$R/dist" "$R/src"
rsync -a --delete "$STAGE/dist/" "$R/dist/"
# Earlier builds' assets stay served for ASSET_HOURS: a page loaded before this deploy (or still loading across it)
# fetches its lazy chunks and workers by their old content-hashed names. Each release carries the files of the kept
# releases that this build lacks, hard-linked (they were written by their own build; that mtime ages them out).
for old in "$C"/releases/*/dist/assets; do
  [ -d "$old" ] && [ "$old" != "$R/dist/assets" ] || continue
  (cd "$old" && find . -type f -mmin -$((ASSET_HOURS * 60)) -print0) | while IFS= read -r -d '' f; do
    [ -e "$R/dist/assets/$f" ] && continue
    mkdir -p "$(dirname "$R/dist/assets/$f")"
    ln "$old/$f" "$R/dist/assets/$f" 2>/dev/null || cp -p "$old/$f" "$R/dist/assets/$f"
  done || echo "warning: earlier assets from $old not all carried"
done
# Server sources (only what the units run); keep an installed server/node_modules across a redeploy of this release.
rsync -a --delete --exclude /server/node_modules --exclude /game --exclude /pipeline --exclude /dist --exclude /work --exclude /.git \
  --exclude /version.json --exclude /tiles.json "$STAGE/" "$R/src/"
cd "$R/src"; echo "$SERVER_TREE" > server.tree; echo "$WEB_TREE" > web.tree
prev=$C/$(readlink "$C/mp" 2>/dev/null || true)
if [ -d server/node_modules ] && [ "$(cat server/node_modules/.lock.sha 2>/dev/null)" = "$(sha256sum < server/package-lock.json)" ]; then
  :  # a redeploy of this release
elif [ "$prev" != "$C/" ] && [ "$prev" != "$R" ] && [ -d "$prev/src/server/node_modules" ] && cmp -s "$prev/src/server/package-lock.json" server/package-lock.json; then
  rm -rf server/node_modules; cp -a "$prev/src/server/node_modules" server/
else
  rm -rf server/node_modules
  npm ci --prefix server --ignore-scripts --omit=dev --no-audit --no-fund --loglevel=error
fi
sha256sum < server/package-lock.json > server/node_modules/.lock.sha
# Tile pin (hot-read by city-tiles).
mv "$STAGE/tiles.json" "$C/tiles.json.tmp" && mv "$C/tiles.json.tmp" "$C/tiles.json"
# Switch, install units, restart what changed, prune old releases (never the ones `current`, `mp` or `web` use).
ln -sfn "releases/$SHA" "$C/current.new" && mv -T "$C/current.new" "$C/current"
changed=''
for f in "$C"/current/src/tools/share/units/*.service "$C"/current/src/tools/share/units/*.socket; do
  u=$(basename "$f")
  cmp -s "$f" "$U/$u" || { cp "$f" "$U/"; changed="$changed ${u%.service}"; }
done
systemctl --user daemon-reload
systemctl --user enable -q city-web city-tiles city-mp city-forwarder city-web.socket city-tiles.socket
# city-web serves the game from current/dist (re-read per request) and city-tiles hot-reads tiles.json, so a deploy
# restarts them only when their code (`web`: the release whose tools/dev and tile proxy they run) or units change.
# A restart cuts the slow requests it cannot drain (a city service prebuilding answers manifests in ~20 s).
restart_web=0
[ "$(cat "$C/web/src/web.tree" 2>/dev/null || true)" = "$WEB_TREE" ] || restart_web=1
for u in city-web city-tiles city-web.socket city-tiles.socket; do
  [[ " $changed " == *" $u "* ]] && restart_web=1
  systemctl --user is-active -q "$u" || restart_web=1
done
if [ "$restart_web" = 1 ]; then ln -sfn "releases/$SHA" "$C/web.new" && mv -T "$C/web.new" "$C/web"; fi
# city-web and city-tiles accept on sockets systemd holds across their restarts (requests wait instead of failing).
# A new or changed socket takes over the port its service bound itself (or the old socket): stop the service first.
for s in city-tiles city-web; do
  if [[ " $changed " == *" $s.socket "* ]] || ! systemctl --user is-active -q "$s.socket"; then
    systemctl --user stop "$s"; systemctl --user restart "$s.socket"
  fi
done
mp_tree=$(cat "$C/mp/src/server.tree" 2>/dev/null || true)
if [ "$mp_tree" != "$SERVER_TREE" ] || [[ " $changed " == *' city-mp '* ]] || ! systemctl --user is-active -q city-mp; then
  ln -sfn "releases/$SHA" "$C/mp.new" && mv -T "$C/mp.new" "$C/mp"
  systemctl --user restart city-mp; echo 'city-mp restarted'
fi
if [ "$restart_web" = 1 ]; then
  systemctl --user restart city-tiles city-web; echo 'city-tiles and city-web restarted'
else
  echo 'city-tiles and city-web keep running (same code; the new game is served from current/dist)'
fi
if [[ " $changed " == *' city-forwarder '* ]]; then systemctl --user restart city-forwarder; else systemctl --user start city-forwarder; fi
keep="$(readlink "$C/current") $(readlink "$C/mp") $(readlink "$C/web")"
ls -1dt "$C"/releases/*/ | tail -n +$((KEEP + 1)) | while read -r old; do
  case " $keep " in *" releases/$(basename "$old") "*) ;; *) rm -rf "$old";; esac
done
REMOTE

# 5. The public URL serves this commit and healthy tiles.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  live=$(curl -fsS -m 5 "https://$PUBLIC_HOST/version.json" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["sha"])' 2>/dev/null || true)
  [ "$live" = "$SHA" ] && break
  sleep 2
done
[ "$live" = "$SHA" ] || { say "FAILED: https://$PUBLIC_HOST/version.json serves '${live:-nothing}'"; exit 1; }
health=$(curl -fsS -m 5 "https://$PUBLIC_HOST/coarse/health" || true)
say "live at https://$PUBLIC_HOST/ (tiles ${FOLLOW:+newest finished $FOLLOW<N>, else }$VERSION, /coarse/health $health)"
