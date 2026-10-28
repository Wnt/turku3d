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
DRAFT_SOURCE=${TURKU3D_DRAFT_SOURCE:-$DRAFT_HOME/repo}
export PYTHONPATH="$DRAFT_SOURCE/pipeline"
export TURKU3D_GLTF_TOOLS="$DRAFT_HOME/tools/npm"
export TURKU3D_MESHOPT_DIR="$TURKU3D_GLTF_TOOLS/node_modules/meshoptimizer"
case "$(uname -m)" in
aarch64 | arm64)
  tool_arch=arm64
  node_arch=arm64
  ;;
x86_64)
  tool_arch=x86_64
  node_arch=x64
  ;;
*)
  echo 'Unsupported deployment architecture' >&2
  exit 1
  ;;
esac
export PATH="$DRAFT_HOME/envs/pipeline/bin:$DRAFT_HOME/tools/node-v24.18.0-linux-$node_arch/bin:$DRAFT_HOME/tools/KTX-Software-4.4.2-Linux-$tool_arch/bin:$DRAFT_HOME/tools/npm/node_modules/.bin:$PATH"
export PROJ_DATA="$DRAFT_HOME/envs/pipeline/share/proj"
export GDAL_DATA="$DRAFT_HOME/envs/pipeline/share/gdal"
# Four processes must not each start a machine-sized native thread pool.
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
cd "$DRAFT_SOURCE"
exec "$@"
