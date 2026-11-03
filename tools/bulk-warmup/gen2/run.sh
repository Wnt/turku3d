#!/usr/bin/env bash
set -euo pipefail
BULK_ROOT=${BULK_ROOT:-/root/turku3d-bulk-gen2}
export REPO=${REPO:-$BULK_ROOT/repo}
export TURKU3D_DATA=${TURKU3D_DATA:-$BULK_ROOT/data}
export TURKU3D_WORK=${TURKU3D_WORK:-$BULK_ROOT/work}
export TURKU3D_SHARED_CACHE=${TURKU3D_SHARED_CACHE:-$BULK_ROOT/farm/cache}
export PYTHONNOUSERSITE=1 PYTHONPATH="$REPO/pipeline:$REPO/textures:$REPO/scripts/dev"
export TURKU3D_GLTF_TOOLS="$BULK_ROOT/tools/npm"
export TURKU3D_MESHOPT_DIR="$TURKU3D_GLTF_TOOLS/node_modules/meshoptimizer"
export PATH="$BULK_ROOT/envs/pipeline/bin:$BULK_ROOT/tools/node-v24.18.0-linux-x64/bin:$BULK_ROOT/tools/KTX-Software-4.4.2-Linux-x86_64/bin:$BULK_ROOT/tools/npm/node_modules/.bin:$PATH"
export PROJ_DATA="$BULK_ROOT/envs/pipeline/share/proj" GDAL_DATA="$BULK_ROOT/envs/pipeline/share/gdal"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
export BLIS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export RAYON_NUM_THREADS=${TURKU3D_THREADS_PER_WORKER:-4}
export NUMBA_NUM_THREADS=$RAYON_NUM_THREADS TURKU3D_JOBS=$RAYON_NUM_THREADS
export GDAL_NUM_THREADS=1
export VIPS_CONCURRENCY=1 UV_THREADPOOL_SIZE=1
cd "$REPO"
exec nice -n 10 "$@"
