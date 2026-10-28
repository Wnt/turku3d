#!/usr/bin/env bash
# Install a private native toolchain; does not reboot or change cloud resources.
set -euo pipefail
test "$(uname -m)" = aarch64
DRAFT_HOME=${TURKU3D_DRAFT_HOME:-$HOME/turku3d-drafts}
test -f "$DRAFT_HOME/repo/tools/remote-drafts/benchmark.py"
test -f "$DRAFT_HOME/repo/pipeline/env/linux-aarch64.lock.txt"
mkdir -p "$DRAFT_HOME"/{tools/bin,tools/npm,envs,reports,work/textures/assembly}
cd "$DRAFT_HOME"

if [ ! -x tools/bin/micromamba ]; then
  curl -fL --retry 3 https://micro.mamba.pm/api/micromamba/linux-aarch64/2.9.0 -o tools/micromamba.tar.bz2
  tar -xjf tools/micromamba.tar.bz2 -C tools bin/micromamba
fi
if [ ! -x tools/node-v24.18.0-linux-arm64/bin/node ]; then
  curl -fL --retry 3 https://nodejs.org/dist/v24.18.0/node-v24.18.0-linux-arm64.tar.xz -o tools/node-v24.18.0-linux-arm64.tar.xz
  curl -fL --retry 3 https://nodejs.org/dist/v24.18.0/SHASUMS256.txt -o tools/node-shasums.txt
  (cd tools && grep ' node-v24.18.0-linux-arm64.tar.xz$' node-shasums.txt | sha256sum -c -)
  tar -xJf tools/node-v24.18.0-linux-arm64.tar.xz -C tools
fi
export PATH="$DRAFT_HOME/tools/node-v24.18.0-linux-arm64/bin:$PATH"
if [ ! -x tools/KTX-Software-4.4.2-Linux-arm64/bin/toktx ]; then
  curl -fL --retry 3 https://github.com/KhronosGroup/KTX-Software/releases/download/v4.4.2/KTX-Software-4.4.2-Linux-arm64.tar.bz2 -o tools/ktx-arm64.tar.bz2
  printf '%s\n' '60382e7b842177b8048bd58ccdc770383f8ef65b94452a25d3afdb55f2405c5a  tools/ktx-arm64.tar.bz2' | sha256sum -c -
  tar -xjf tools/ktx-arm64.tar.bz2 -C tools
fi
if [ ! -x envs/pipeline/bin/python ]; then
  PYTHONNOUSERSITE=1 tools/bin/micromamba create -y -r "$DRAFT_HOME/mamba" \
    -p "$DRAFT_HOME/envs/pipeline" -f repo/pipeline/env/linux-aarch64.lock.txt
fi
# The 20250106 release has no Linux ARM64 wheel/sdist; build its exact upstream tag.
envs/pipeline/bin/python -m pip install \
  'triangle @ git+https://github.com/drufat/triangle.git@595b43eb6682992a0b1012b9671bf860c0e6ae56' \
  jsonschema==4.26.0
cp repo/pipeline/env/package*.json tools/npm/
(cd tools/npm && npm ci)

cat >run.sh <<'RUNNER'
#!/usr/bin/env bash
set -euo pipefail
export TURKU3D_DRAFT_HOME=${TURKU3D_DRAFT_HOME:-$HOME/turku3d-drafts}
export PATH="$TURKU3D_DRAFT_HOME/tools/node-v24.18.0-linux-arm64/bin:$TURKU3D_DRAFT_HOME/tools/KTX-Software-4.4.2-Linux-arm64/bin:$PATH"
exec "$TURKU3D_DRAFT_HOME/repo/tools/remote-drafts/run.sh" "$@"
RUNNER
chmod 755 run.sh
./run.sh python -c 'import pdal, rasterio, geopandas, scipy, shapely, triangle, jsonschema; print("Native pipeline imports OK")'
./run.sh node --version
./run.sh toktx --version
tools/bin/micromamba list -p "$DRAFT_HOME/envs/pipeline" --explicit >reports/environment.lock.txt
./run.sh python -m pip freeze >reports/pip-freeze.txt
sha256sum tools/*.tar.* >reports/tool-archives.sha256
