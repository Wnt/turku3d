#!/usr/bin/env bash
# Shared local farm only. The source checkout is always an immutable registered revision.
set -euo pipefail
: "${REPO:?immutable source snapshot required}"
: "${TURKU3D_WORK:?own output root required}"
TOOLS="$HOME/turku3d-bulk-warmup"
export TURKU3D_DATA="${TURKU3D_DATA:-$HOME/turku3d-local/combined/data}"
export TURKU3D_SANDBOX=loop-tiles TURKU3D_PORT_BASE=5790
export TURKU3D_BROWSER_DIR="$HOME/turku3d-local/loops/tiles/browser"
export TURKU3D_SLOT_DIR="$HOME/turku3d-local/combined/host-slots"
export PYTHONNOUSERSITE=1 PYTHONPATH="$REPO/pipeline:$REPO/textures"
export TURKU3D_GLTF_TOOLS="$TOOLS/tools/npm"
export TURKU3D_MESHOPT_DIR="$TURKU3D_GLTF_TOOLS/node_modules/meshoptimizer"
export PATH="$TOOLS/envs/pipeline/bin:$TOOLS/tools/KTX-Software-4.4.2-Linux-x86_64/bin:$TOOLS/tools/npm/node_modules/.bin:$PATH"
export PROJ_DATA="$TOOLS/envs/pipeline/share/proj" GDAL_DATA="$TOOLS/envs/pipeline/share/gdal"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
cd "$REPO"
exec nice -n 10 "$@"
