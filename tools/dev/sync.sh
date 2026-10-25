#!/usr/bin/env bash
# Mirror the remote tile dist (osgallery-dev:~/turku3d/data/dist) to the Mac (~/turku3d-data/dist).
# One-way, remote -> local; never writes to the remote. manifest.json is copied last so a half-synced mirror
# never advertises tiles it does not have (docs/specs/tiles.md §5).
#
# Usage: tools/dev/sync.sh [--dry-run] [--delete] [--only tiles/collision] [--host osgallery-dev]
# Env:   REMOTE_HOST (default osgallery-dev), REMOTE_DIST (default turku3d/data/dist, relative to remote $HOME),
#        LOCAL_DIST (default ~/turku3d-data/dist)
set -euo pipefail
HOST="${REMOTE_HOST:-osgallery-dev}"
REMOTE_DIST="${REMOTE_DIST:-turku3d/data/dist}"
LOCAL_DIST="${LOCAL_DIST:-$HOME/turku3d-data/dist}"
DRY=() DELETE=() ONLY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run|-n) DRY=(--dry-run); shift ;;
    --delete) DELETE=(--delete --delete-delay); shift ;;
    --only) ONLY="${2%/}"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
SRC="${HOST}:${REMOTE_DIST}/${ONLY:+$ONLY/}"
DST="${LOCAL_DIST}/${ONLY:+$ONLY/}"
mkdir -p "$DST"
if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" "test -d ${REMOTE_DIST}/${ONLY}"; then
  echo "remote ${HOST}:~/${REMOTE_DIST}/${ONLY} does not exist (nothing built yet?)" >&2
  exit 1
fi
RSYNC=(rsync -a --partial --info=stats1,progress2 --human-readable "${DRY[@]}" "${DELETE[@]}")
# macOS ships an old openrsync without --info; fall back to -v --stats
if ! rsync --help 2>&1 | grep -q -- '--info'; then RSYNC=(rsync -a --partial -v --stats "${DRY[@]}" "${DELETE[@]}"); fi
echo "sync ${SRC} -> ${DST}"
# 1) everything except manifests, 2) manifests last
"${RSYNC[@]}" --exclude 'manifest.json' --exclude '*.tmp' "$SRC" "$DST"
"${RSYNC[@]}" --include '*/' --include 'manifest.json' --exclude '*' "$SRC" "$DST"
if [[ ${#DRY[@]} -eq 0 && -f "${LOCAL_DIST}/tiles/manifest.json" ]]; then
  # verify sha256 of every file listed in the manifest
  node -e '
    const fs = require("fs"), path = require("path"), crypto = require("crypto");
    const dir = process.argv[1]; const m = JSON.parse(fs.readFileSync(path.join(dir, "manifest.json")));
    let ok = 0, bad = 0, missing = 0;
    for (const e of [m.tileset, ...Object.values(m.visual ?? {}), ...Object.values(m.collision ?? {})]) {
      if (!e) continue; const f = path.join(dir, e.url);
      if (!fs.existsSync(f)) { missing++; continue; }
      const h = crypto.createHash("sha256").update(fs.readFileSync(f)).digest("hex");
      if (h === e.sha256) ok++; else { bad++; console.error("sha256 mismatch:", e.url); }
    }
    console.log(`manifest check: ${ok} ok, ${bad} mismatched, ${missing} missing`);
    process.exit(bad || missing ? 1 : 0);' "${LOCAL_DIST}/tiles"
fi
