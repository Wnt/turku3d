#!/usr/bin/env bash
# Install the native Mac toolchain. Never changes shell startup files or sandbox paths.
set -euo pipefail
REPO=${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
ROOT="$HOME/turku3d"
ENV="$ROOT/envs/pipeline"
MM="$ROOT/tools/bin/micromamba"
export MAMBA_ROOT_PREFIX="$ROOT/tools/mamba" PYTHONNOUSERSITE=1
[[ $(uname -sm) == 'Darwin arm64' ]] || { echo 'This bootstrap requires macOS arm64' >&2; exit 1; }
mkdir -p "$ROOT/tools/bin" "$ROOT/envs"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
if [[ ! -x $MM ]]; then
  curl -fsSL --retry 3 https://micro.mamba.pm/api/micromamba/osx-arm64/2.8.1 -o "$tmp/mm.tar.bz2"
  tar xjf "$tmp/mm.tar.bz2" -C "$tmp" bin/micromamba
  mv "$tmp/bin/micromamba" "$MM"
fi
if [[ ! -x $ENV/bin/python ]]; then
  if [[ -f $REPO/pipeline/env/osx-arm64.lock.txt ]]; then
    "$MM" create -y -p "$ENV" -f "$REPO/pipeline/env/osx-arm64.lock.txt"
  else
    "$MM" create -y -p "$ENV" -f "$REPO/pipeline/env/environment.yml"
  fi
elif [[ ${1:-} == --update ]]; then
  "$MM" install -y -p "$ENV" -f "$REPO/pipeline/env/environment.yml"
fi
"$ENV/bin/python" -m pip install --disable-pip-version-check -q triangle==20250106
"$MM" env export -p "$ENV" --explicit > "$ROOT/envs/pipeline.lock.txt"
NPMDIR="$ROOT/tools/npm"
mkdir -p "$NPMDIR"
if [[ ! -f $NPMDIR/package-lock.json ]] || ! cmp -s "$REPO/pipeline/env/package-lock.json" "$NPMDIR/package-lock.json" \
    || [[ ! -x $NPMDIR/node_modules/.bin/gltf-transform ]]; then
  cp "$REPO/pipeline/env/package.json" "$REPO/pipeline/env/package-lock.json" "$NPMDIR/"
  (cd "$NPMDIR" && npm ci --no-audit --no-fund)
fi
for tool in gltf-transform gltfpack; do ln -sf "$NPMDIR/node_modules/.bin/$tool" "$ROOT/tools/bin/$tool"; done
RD="$ROOT/tools/roofer-1.0.0"
if [[ ! -x $RD/bin/roofer ]]; then
  curl -fsSL --retry 3 https://github.com/3DBAG/roofer/releases/download/v1.0.0/roofer-macOS-arm64-v1.0.0.tar.gz -o "$tmp/roofer.tgz"
  mkdir -p "$RD"
  tar xzf "$tmp/roofer.tgz" -C "$RD"
  chmod +x "$RD/bin/roofer"
fi
cat > "$ROOT/tools/bin/roofer" <<WRAP
#!/usr/bin/env bash
export GDAL_DATA="$RD/share/gdal" PROJ_DATA="$RD/share/proj"
exec "$RD/bin/roofer" "\$@"
WRAP
chmod +x "$ROOT/tools/bin/roofer"
KD="$ROOT/tools/ktx-4.4.2"
if [[ ! -x $KD/usr/local/bin/toktx ]]; then
  curl -fsSL --retry 3 https://github.com/KhronosGroup/KTX-Software/releases/download/v4.4.2/KTX-Software-4.4.2-Darwin-arm64.pkg -o "$tmp/ktx.pkg"
  pkgutil --expand-full "$tmp/ktx.pkg" "$tmp/ktx"
  mkdir -p "$KD"
  while IFS= read -r payload; do cp -R "$payload"/. "$KD/"; done < <(find "$tmp/ktx" -type d -name Payload)
fi
for tool in toktx ktx; do ln -sf "$KD/usr/local/bin/$tool" "$ROOT/tools/bin/$tool"; done
cat > "$ROOT/envs/pipeline.env" <<ENVSH
export PATH="$ENV/bin:$ROOT/tools/bin:\$PATH"
export PROJ_DATA="$ENV/share/proj" GDAL_DATA="$ENV/share/gdal" PYTHONNOUSERSITE=1
export TURKU3D_MESHOPT_DIR="$NPMDIR/node_modules/meshoptimizer"
ENVSH
bash "$REPO/pipeline/env/versions.sh"
