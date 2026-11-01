#!/usr/bin/env bash
# Local-area environment on a host that holds a bulk warm-up copy (osgallery-dev): the copy's x86
# toolchain, this checkout's code (rsynced to $LOCAL/repo) and a separate data/work root, so the
# bulk copy stays a faithful copy of production. Everything runs under nice.
set -euo pipefail
LOCAL=${TURKU3D_LOCAL_ROOT:-$HOME/turku3d-local}
TOOLS=${TURKU3D_TOOLCHAIN:-$HOME/turku3d-bulk-warmup}
export TURKU3D_DATA="$LOCAL/areas-root/data"
export TURKU3D_WORK="$LOCAL/areas-root/work"
export TURKU3D_SANDBOX=local-areas
export TURKU3D_PORT_BASE=7790
export TURKU3D_BROWSER_DIR="$LOCAL/areas-root/browser"
export TURKU3D_SLOT_DIR="$LOCAL/areas-root/host-slots"
export PYTHONNOUSERSITE=1
export PYTHONPATH="$LOCAL/repo/pipeline"
export TURKU3D_GLTF_TOOLS="$TOOLS/tools/npm"
export TURKU3D_MESHOPT_DIR="$TURKU3D_GLTF_TOOLS/node_modules/meshoptimizer"
export PATH="$TOOLS/envs/pipeline/bin:$TOOLS/tools/KTX-Software-4.4.2-Linux-x86_64/bin:$TOOLS/tools/npm/node_modules/.bin:$PATH"
export PROJ_DATA="$TOOLS/envs/pipeline/share/proj"
export GDAL_DATA="$TOOLS/envs/pipeline/share/gdal"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
cd "$LOCAL/repo"
exec nice -n "${NICE:-10}" "$@"
