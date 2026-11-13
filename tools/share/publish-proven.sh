#!/usr/bin/env bash
# Publishes the proven-plus world to https://city.lab.madekivi.fi/proven/ and nowhere else.
#
#   tools/share/publish-proven.sh [--rev REV]      # default REV: local/proven-plus
#
# local/proven-plus is the proven city.madekivi.fi release (63b0b015) plus small additions on top. Its world is the
# proven one: the client of REV, and the proven generator's tiles from the lab's /coarse (city-tiles -> the farm at the
# pin in ~/turku3d-local/city/tiles.json, the same pin and `follow` family city.madekivi.fi serves). This script only
# ships the client; a generator addition reaches players through a new farm version and pin (status/w2-baseline.md).
#
# What one run does (the lab's only machine is osgallery-dev; it never talks to OCI, city.madekivi.fi or Pages):
#  1. Ships `git archive REV` (game, tools/dev, tools/share, server, night lighting) to a stage dir on osgallery-dev and
#     builds the client THERE exactly as tools/share/deploy.sh built the proven release (VITE_SHARE_HOSTS=
#     city.lab.madekivi.fi,city.madekivi.fi, VITE_PUBLIC_RELEASE=1, deps cached by lockfile hash in
#     ~/turku3d-local/city/deps/<hash>), as a batch job in tilebuilds.slice (CPUWeight 10). The same sources and
#     settings give the same content-hashed file names as city.madekivi.fi's build (check: compare index.html).
#  2. Under the lab's deploy lock: installs the dist as ~/turku3d-local/city/proven/releases/<sha>/dist (Brotli sidecars,
#     earlier releases' assets carried for pages still open), switches ~/turku3d-local/city/proven/current, keeps 3.
#  3. Checks https://city.lab.madekivi.fi/proven/version.json names REV.
# city-web serves it through the mount `/proven=%h/turku3d-local/city/proven/current/dist` of its unit
# (tools/share/units/city-web.service on local/loop-integration, installed by publish-w2.sh); the client's vite base is
# './' and its services are absolute (/coarse/, the multiplayer socket), so the same build works under /proven/.
set -euo pipefail

SSH_HOST=osgallery-dev
PUBLIC_HOST=city.lab.madekivi.fi
PREFIX=proven
SHARE_HOSTS=city.lab.madekivi.fi,city.madekivi.fi
REPO=$(cd "$(dirname "$0")/../.." && pwd)
REV=local/proven-plus
while [ $# -gt 0 ]; do
  case "$1" in
    --rev) REV=$2; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ "$SSH_HOST" = osgallery-dev ] && [ "$PUBLIC_HOST" = city.lab.madekivi.fi ] || { echo 'this script publishes to the lab only' >&2; exit 2; }
remote() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$SSH_HOST" "$@"; }

SHA=$(git -C "$REPO" rev-parse --verify "$REV^{commit}")
SHORT=${SHA:0:8}
SUBJECT=$(git -C "$REPO" log -1 --format=%s "$SHA")
say() { printf 'publish-proven %s: %s\n' "$SHORT" "$*"; }
PHASE=$(date +%s)
phase() { local now; now=$(date +%s); say "$1: $((now - PHASE)) s"; PHASE=$now; }

C=turku3d-local/city
STAGE=turku3d-w2/w2-baseline/stage/$SHORT-$$
META=$(mktemp -d)
trap 'rm -rf "$META"' EXIT
TILES=$(remote "cat ~/$C/tiles.json")
python3 - "$META/version.json" "$SHA" "$SUBJECT" "$TILES" <<'PY'
import json, sys, time
path, sha, subject, tiles = sys.argv[1:]
t = json.loads(tiles)
json.dump(dict(sha=sha, subject=subject, tiles=t['version'], follow=t.get('follow', ''), exclude=t.get('exclude', []),
               world='proven-plus', built=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())), open(path, 'w'), indent=1)
PY

remote "mkdir -p ~/$STAGE ~/$C/$PREFIX/releases"
git -C "$REPO" archive --add-file="$META/version.json" "$SHA" game tools/dev tools/share server pipeline/turku3d/night_lighting.json \
  | remote "tar -x -C ~/$STAGE"
phase 'shipped the sources'

say "building on $SSH_HOST (tilebuilds.slice, share hosts $SHARE_HOSTS)"
remote "bash -s -- $SHA $STAGE $SHARE_HOSTS" <<'REMOTE'
set -euo pipefail
SHA=$1 STAGE=$HOME/$2 SHARE_HOSTS=$3 C=$HOME/turku3d-local/city
LOCK=$(sha256sum < "$STAGE/game/package-lock.json" | cut -c1-16)
D=$C/deps/$LOCK
if [ ! -f "$D/.ok" ]; then
  # deploy.sh's cache layout; a new lockfile installs once, under the lab's deploy lock like deploy.sh.
  flock -w 900 "$C/deploy.lock" bash -c "rm -rf '$D' && mkdir -p '$D' && cp '$STAGE/game/package.json' '$STAGE/game/package-lock.json' '$D/' \
    && cd '$D' && npm ci --ignore-scripts --no-audit --no-fund --loglevel=error && touch '$D/.ok'"
fi
ln -sfn "$D/node_modules" "$STAGE/game/node_modules"
LOG=$STAGE/build.log
UNIT=w2-baseline-build-${SHA:0:8}-$$
if ! systemd-run --user --quiet --wait --collect --unit "$UNIT" --slice=tilebuilds.slice -p CPUWeight=10 -p RuntimeMaxSec=1200 \
    --working-directory="$STAGE/game" \
    --setenv=TURKU3D_BUILD_COMMIT="$SHA" --setenv=TURKU3D_WORK="$STAGE/work" --setenv=VITE_SHARE_HOSTS="$SHARE_HOSTS" \
    --setenv=VITE_PUBLIC_RELEASE=1 --setenv=PATH="$PATH" \
    bash -c "./node_modules/.bin/vite build --logLevel warn --outDir '$STAGE/dist' > '$LOG' 2>&1"; then
  tail -30 "$LOG"; echo 'client build failed'; exit 1
fi
tail -5 "$LOG"
find "$STAGE/dist" -name '*.map' -delete
cp "$STAGE/version.json" "$STAGE/dist/version.json"
nice -n 10 node "$STAGE/tools/share/precompress.mjs" "$STAGE/dist"
REMOTE
phase 'built the client'

remote "flock -w 900 ~/$C/deploy.lock bash -s -- $SHA $STAGE $PREFIX" <<'REMOTE'
set -euo pipefail
SHA=$1 STAGE=$HOME/$2 PREFIX=$3 ASSET_HOURS=12 KEEP=3
P=$HOME/turku3d-local/city/$PREFIX R=$HOME/turku3d-local/city/$PREFIX/releases/$SHA
mkdir -p "$R/dist"
rsync -a --delete "$STAGE/dist/" "$R/dist/"
# Pages loaded before this publish fetch their lazy chunks and workers by their old content-hashed names.
for old in "$P"/releases/*/dist/assets; do
  [ -d "$old" ] && [ "$old" != "$R/dist/assets" ] || continue
  (cd "$old" && find . -type f -mmin -$((ASSET_HOURS * 60)) -print0) | while IFS= read -r -d '' f; do
    [ -e "$R/dist/assets/$f" ] && continue
    mkdir -p "$(dirname "$R/dist/assets/$f")"
    ln "$old/$f" "$R/dist/assets/$f" 2>/dev/null || cp -p "$old/$f" "$R/dist/assets/$f"
  done || echo "warning: earlier assets from $old not all carried"
done
ln -sfn "releases/$SHA" "$P/current.new" && mv -T "$P/current.new" "$P/current"
keep=$(readlink "$P/current")
ls -1dt "$P"/releases/*/ | tail -n +$((KEEP + 1)) | while read -r old; do
  name=$(basename "$old")
  if [[ "$name" =~ ^[a-f0-9]{40}$ ]] && [ "releases/$name" != "$keep" ]; then rm -rf "$P/releases/$name"; fi
done
rm -rf "$STAGE"
echo "current: $keep"
grep -q -- "--mount /$PREFIX=" "$HOME/.config/systemd/user/city-web.service" \
  || echo "WARNING: city-web.service has no /$PREFIX mount (install local/loop-integration's unit via publish-w2.sh)"
REMOTE
phase 'published'

live=''
for _ in 1 2 3 4 5 6 7 8 9 10; do
  live=$(curl -fsS -m 5 "https://$PUBLIC_HOST/$PREFIX/version.json" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["sha"])' 2>/dev/null || true)
  [ "$live" = "$SHA" ] && break
  sleep 2
done
[ "$live" = "$SHA" ] || { say "FAILED: https://$PUBLIC_HOST/$PREFIX/version.json serves '${live:-nothing}'"; exit 1; }
say "live at https://$PUBLIC_HOST/$PREFIX/ (tiles: the lab's /coarse pin, $(curl -fsS -m 5 "https://$PUBLIC_HOST/coarse/health" || echo 'health unknown'))"
