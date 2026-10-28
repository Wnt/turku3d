#!/usr/bin/env bash
# Dedicated Linux deployment; never source the legacy host's pipeline.env.
set -euo pipefail
DRAFT_HOME=${TURKU3D_DRAFT_HOME:-$HOME/turku3d-drafts}
export TURKU3D_DATA="$DRAFT_HOME/data"
export TURKU3D_WORK="$DRAFT_HOME/work"
export TURKU3D_SANDBOX=remote-drafts
export TURKU3D_PORT_BASE=7760
export TURKU3D_BROWSER_DIR="$DRAFT_HOME/browser"
export TURKU3D_SLOT_DIR="$DRAFT_HOME/host-slots"
export PYTHONNOUSERSITE=1
export PYTHONPATH="$DRAFT_HOME/repo/pipeline"
export TURKU3D_GLTF_TOOLS="$DRAFT_HOME/tools/npm"
export TURKU3D_MESHOPT_DIR="$TURKU3D_GLTF_TOOLS/node_modules/meshoptimizer"
export PATH="$DRAFT_HOME/envs/pipeline/bin:$DRAFT_HOME/tools/KTX-Software-4.4.2-Linux-x86_64/bin:$DRAFT_HOME/tools/npm/node_modules/.bin:$PATH"
export PROJ_DATA="$DRAFT_HOME/envs/pipeline/share/proj"
export GDAL_DATA="$DRAFT_HOME/envs/pipeline/share/gdal"
# Four processes must not each start a machine-sized native thread pool.
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
cd "$DRAFT_HOME/repo"
exec "$@"
