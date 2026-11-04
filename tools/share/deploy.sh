#!/usr/bin/env bash
# Public share of the local next-gen game at https://city.lab.madekivi.fi, served from osgallery-dev without the Mac.
#
#   tools/share/deploy.sh [REV]    # default: local/loop-integration of ~/turku3d/wt/loop-integration/repo
#
# Builds REV on this Mac with VITE_SHARE_HOSTS=city.lab.madekivi.fi (same-origin tiles, point cloud and multiplayer;
# game/src/core/localOnly.ts) and VITE_PUBLIC_RELEASE=1 (the Pages start menu, no editor or capture), copies the dist
# and the server sources to osgallery-dev ~/turku3d-local/city/releases/<sha>, pins the tile proxy to the local game's
# tile version (~/turku3d/loops/integrator/tile-service.json), switches `current`, installs the units, restarts what
# changed and checks the public URL. /version.json names the deployed commit.
#
# Tiles resolve per tile (scripts/dev/shared-tile-proxy.mjs `follow`): newest first through the farm's train<N>
# aliases on the pin's base generation, then the pin. A tile comes from the newest train that finished it; tiles a city
# pass finishes later, and new trains, show up on their own with no redeploy.
#
# systemd --user units on osgallery-dev (linger; Restart=always; enabled for boot), sources in tools/share/units:
#   city-web        tools/dev/serve-dist.mjs --public, 127.0.0.1:7780: the game, /pointcloud -> /bulk/turku3d/lidarpc/copc,
#                   /coarse -> city-tiles, WebSocket + /live.json + /scores/ -> city-mp; GET/HEAD only
#   city-tiles      scripts/dev/shared-tile-proxy.mjs, 127.0.0.1:7781 -> farm 192.168.1.114:7950 (/v/<pin>) and the
#                   scenery server 127.0.0.1:7947; pin in ~/turku3d-local/city/tiles.json (hot-read)
#   city-mp         server/index.js, 127.0.0.1:7782, TURKU_MP_ORIGINS=https://city.lab.madekivi.fi, own scores dir;
#                   restarted only when server/ changes (players reconnect with their session)
#   city-forwarder  forwarder-agent --tunnel city:http:city.lab.madekivi.fi:7780 (token: ~/.config/turku3d/forwarder.env)
# The farm, scenery and COPC services the share reads run as shared-tiles, local-horizon and lidar-copc user units.
#
# Env overrides: TURKU3D_SHARE_REPO, TURKU3D_SHARE_SSH (osgallery-dev), TURKU3D_SHARE_PIN (a tile-service.json),
# TURKU3D_SHARE_SCENERY (http://127.0.0.1:7947), TURKU3D_SHARE_WORK (Mac build dir), TURKU3D_SHARE_FOLLOW (alias
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
REV=${1:-local/loop-integration}
KEEP=3

SHA=$(git -C "$REPO" rev-parse --verify "$REV^{commit}")
SHORT=${SHA:0:8}
SERVER_TREE=$(git -C "$REPO" rev-parse "$SHA:server")
say() { printf 'deploy %s: %s\n' "$SHORT" "$*"; }

# 1. Build the committed tree in a detached worktree (vite.config.ts stamps the commit from git).
BUILD="$WORK/builds/$SHA"
if [ ! -f "$BUILD/game/index.html" ]; then
  STAGE="$WORK/stage/$SHA"
  rm -rf "$STAGE" "$BUILD"
  git -C "$REPO" worktree prune
  git -C "$REPO" worktree add -q --detach "$STAGE" "$SHA"
  trap 'git -C "$REPO" worktree remove --force "$STAGE" >/dev/null 2>&1 || true' EXIT
  ln -s "$(cd "$REPO/game/node_modules" && pwd -P)" "$STAGE/game/node_modules"
  say "building (vite, share host $PUBLIC_HOST)"
  (cd "$STAGE/game" && TURKU3D_WORK="$BUILD/work" VITE_SHARE_HOSTS=$PUBLIC_HOST VITE_PUBLIC_RELEASE=1 \
    ./node_modules/.bin/vite build --logLevel warn --outDir "$BUILD/game")
  git -C "$REPO" worktree remove --force "$STAGE"
  trap - EXIT
fi
VERSION=$(python3 - "$PIN" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))['version'])
PY
)
SUBJECT=$(git -C "$REPO" log -1 --format=%s "$SHA")
python3 - "$BUILD/game/version.json" "$SHA" "$SUBJECT" "$VERSION" "$FOLLOW" "$EXCLUDE" <<'PY'
import json, sys, time
path, sha, subject, tiles, follow, exclude = sys.argv[1:]
json.dump(dict(sha=sha, subject=subject, tiles=tiles, follow=follow, exclude=exclude.split(),
               built=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())), open(path, 'w'), indent=1)
PY

# 2. Upload the dist and the server sources (only what the units run) into a new release.
R="turku3d-local/city/releases/$SHA"
ssh "$SSH_HOST" "mkdir -p ~/turku3d-local/city/releases ~/turku3d-local/city/mp-scores ~/turku3d-local/city/work ~/$R/src"
say "uploading $(du -sh "$BUILD/game" | cut -f1) dist"
rsync -a --delete --exclude '*.map' "$BUILD/game/" "$SSH_HOST:$R/dist/"
git -C "$REPO" archive "$SHA" tools/dev scripts/dev/shared-tile-proxy.mjs server tools/share | ssh "$SSH_HOST" "tar -x -C ~/$R/src"
ssh "$SSH_HOST" "set -e; cd ~/$R/src; echo $SERVER_TREE > server.tree
  prev=\$(readlink ~/turku3d-local/city/mp 2>/dev/null || true); prev=~/turku3d-local/city/\$prev
  if [ -d server/node_modules ] && [ \"\$(cat server/node_modules/.lock.sha 2>/dev/null)\" = \"\$(sha256sum < server/package-lock.json)\" ]; then
    :  # a redeploy of this release
  elif [ \"\$prev\" != ~/turku3d-local/city/ ] && [ \"\$prev\" != ~/$R ] && [ -d \"\$prev/src/server/node_modules\" ] && cmp -s \"\$prev/src/server/package-lock.json\" server/package-lock.json; then
    cp -a \"\$prev/src/server/node_modules\" server/
  else
    npm ci --prefix server --ignore-scripts --omit=dev --no-audit --no-fund --loglevel=error
  fi
  sha256sum < server/package-lock.json > server/node_modules/.lock.sha"

# 3. Tile pin (hot-read by city-tiles): the local game's version on the same farm, the alias family followed per
#    tile (newest finished compatible train first), scenery from osgallery-dev itself.
python3 - "$PIN" "$SCENERY" "$FOLLOW" "$EXCLUDE" <<'PY' | ssh "$SSH_HOST" 'cat > ~/turku3d-local/city/tiles.json.tmp && mv ~/turku3d-local/city/tiles.json.tmp ~/turku3d-local/city/tiles.json'
import json, sys
pin = json.load(open(sys.argv[1]))
print(json.dumps(dict(service=pin['service'], version=pin['version'], scenery=sys.argv[2],
                      **(dict(follow=sys.argv[3], exclude=sys.argv[4].split()) if sys.argv[3] else {}))))
PY

# 4. Switch, install units, restart what changed, prune old releases (never the ones `current` or `mp` use).
ssh "$SSH_HOST" "set -e; C=~/turku3d-local/city; U=~/.config/systemd/user
  ln -sfn releases/$SHA \$C/current.new && mv -T \$C/current.new \$C/current
  changed=''
  for f in \$C/current/src/tools/share/units/*.service; do
    cmp -s \"\$f\" \"\$U/\$(basename \$f)\" || { cp \"\$f\" \$U/; changed=\"\$changed \$(basename \$f .service)\"; }
  done
  systemctl --user daemon-reload
  systemctl --user enable -q city-web city-tiles city-mp city-forwarder
  mp_tree=\$(cat \$C/mp/src/server.tree 2>/dev/null || true)
  if [ \"\$mp_tree\" != $SERVER_TREE ] || [[ \" \$changed \" == *' city-mp '* ]] || ! systemctl --user is-active -q city-mp; then
    ln -sfn releases/$SHA \$C/mp.new && mv -T \$C/mp.new \$C/mp
    systemctl --user restart city-mp; echo 'city-mp restarted'
  fi
  systemctl --user restart city-tiles city-web
  [[ \" \$changed \" == *' city-forwarder '* ]] && systemctl --user restart city-forwarder || systemctl --user start city-forwarder
  keep=\"\$(readlink \$C/current) \$(readlink \$C/mp)\"
  ls -1dt \$C/releases/*/ | tail -n +$((KEEP + 1)) | while read -r old; do
    case \" \$keep \" in *\" releases/\$(basename \$old) \"*) ;; *) rm -rf \"\$old\";; esac
  done"
find "$WORK/builds" -mindepth 1 -maxdepth 1 -type d ! -name "$SHA" -mtime +2 -exec rm -rf {} + 2>/dev/null || true

# 5. The public URL serves this commit and healthy tiles.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  live=$(curl -fsS -m 5 "https://$PUBLIC_HOST/version.json" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["sha"])' 2>/dev/null || true)
  [ "$live" = "$SHA" ] && break
  sleep 2
done
[ "$live" = "$SHA" ] || { say "FAILED: https://$PUBLIC_HOST/version.json serves '${live:-nothing}'"; exit 1; }
health=$(curl -fsS -m 5 "https://$PUBLIC_HOST/coarse/health" || true)
say "live at https://$PUBLIC_HOST/ (tiles ${FOLLOW:+newest finished $FOLLOW<N>, else }$VERSION, /coarse/health $health)"
