#!/usr/bin/env bash
# Create (or refresh) the local-area data/work root next to a bulk warm-up copy. Idempotent.
#
# The bulk copy's raw units are linked entry by entry (read-only use; builds never write raw/),
# its registries are copied so new downloads (pistepilvi LAZ, building registers) land only here.
# Street-level imagery (raw/streetview) is not linked.
set -euo pipefail
LOCAL=${TURKU3D_LOCAL_ROOT:-$HOME/turku3d-local}
TOOLS=${TURKU3D_TOOLCHAIN:-$HOME/turku3d-bulk-warmup}
B=$TOOLS/data
D=$LOCAL/areas-root/data
W=$LOCAL/areas-root/work
mkdir -p "$D/raw"/{laz/pistepilvi,wfs,ortho/ortho2022to,ortho/ortho2025,osm,prefetch} "$D/cache" \
  "$W" "$LOCAL/areas-root/browser" "$LOCAL/areas-root/host-slots"
for d in archive facade-approvals facade-assets facade-characterization facade-params textures; do
  [ -e "$D/$d" ] || ln -s "$B/$d" "$D/$d"
done
[ -e "$D/raw/laz/maanpinta" ] || ln -s "$B/raw/laz/maanpinta" "$D/raw/laz/maanpinta"
for k in laz ortho osm wfs; do
  for f in MANIFEST.jsonl SHA256SUMS; do
    [ -f "$B/raw/$k/$f" ] && [ ! -f "$D/raw/$k/$f" ] && cp "$B/raw/$k/$f" "$D/raw/$k/$f"
  done
done
link_entries() {  # link every entry of $1 into $2 that $2 does not have yet
  local src=$1 dst=$2 n=0 e
  for e in "$src"/*; do
    [ -e "$dst/$(basename "$e")" ] || { ln -s "$e" "$dst/"; n=$((n + 1)); }
  done
  echo "linked $n new entries into $dst"
}
link_entries "$B/raw/wfs" "$D/raw/wfs"
link_entries "$B/raw/osm" "$D/raw/osm"
link_entries "$B/raw/ortho/ortho2022to" "$D/raw/ortho/ortho2022to"
link_entries "$B/raw/ortho/ortho2025" "$D/raw/ortho/ortho2025"
# Small work-root state the generator reads (facade rules, texture assembly).
[ -e "$W/facade-rules-models" ] || cp -a "$TOOLS/work/facade-rules-models" "$W/"
target=$(basename "$(readlink "$TOOLS/work/facade-rules.json")")
ln -sfn "$W/facade-rules-models/$target" "$W/facade-rules.json"
[ -e "$W/textures" ] || cp -a "$TOOLS/work/textures" "$W/"
echo "root ready: $LOCAL/areas-root"
