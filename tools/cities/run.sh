#!/usr/bin/env bash
# Run a pipeline command for one city on a host with the bulk warm-up toolchain (osgallery-dev, UpCloud):
#   tools/cities/run.sh espoo python -m turku3d.coarse build t0_1_1 --out /tmp/t
# The city's data root is $CITIES/<city>/data (raw linked from the bulk pool, see setup-root.sh) and its
# writable root $CITIES/<city>/work. Code is this checkout. Everything runs under nice.
set -euo pipefail
CITY=${1:?city}; shift
CITIES=${TURKU3D_CITIES_ROOT:-$HOME/turku3d-local/cities}
TOOLS=${TURKU3D_TOOLCHAIN:-$HOME/turku3d-bulk-warmup}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
export TURKU3D_CITY="$CITY"
export TURKU3D_DATA="$CITIES/$CITY/data"
export TURKU3D_WORK="$CITIES/$CITY/work"
export TURKU3D_SANDBOX="city-$CITY"
export TURKU3D_PORT_BASE=${TURKU3D_PORT_BASE:-7970}
export TURKU3D_BROWSER_DIR="$CITIES/$CITY/browser"
export TURKU3D_SLOT_DIR="$CITIES/$CITY/host-slots"
export PYTHONNOUSERSITE=1 PYTHONPATH="$REPO/tools/cities/site:$REPO/pipeline:$REPO/textures"
export TURKU3D_GLTF_TOOLS="$TOOLS/tools/npm"
export TURKU3D_MESHOPT_DIR="$TURKU3D_GLTF_TOOLS/node_modules/meshoptimizer"
export PATH="$TOOLS/envs/pipeline/bin:$TOOLS/tools/KTX-Software-4.4.2-Linux-x86_64/bin:$TOOLS/tools/npm/node_modules/.bin:$PATH"
export PROJ_DATA="$TOOLS/envs/pipeline/share/proj" GDAL_DATA="$TOOLS/envs/pipeline/share/gdal"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 GDAL_NUM_THREADS=1
export RAYON_NUM_THREADS=${RAYON_NUM_THREADS:-2} NUMBA_NUM_THREADS=${RAYON_NUM_THREADS:-2}
cd "$REPO"
# Serving first on a shared host (osgallery-dev, 2026-11-07: load 45-60 on 16 cores from tile builds, and city.lab
# tile requests waited seconds). A service (a `serve` command) runs at normal priority and nices only its own build
# workers (TURKU3D_BUILD_NICE, pipeline/turku3d/coarse/worker_pool.py). Any other command (a build, a pass) runs
# niced and, outside a systemd unit, in the user manager's tilebuilds.slice: each ssh session is its own cgroup that
# weighs as much as all the user services together, whatever its nice; the slice weighs a fifth of a service.
NICE=${NICE:-10}
case " $* " in
  *" serve "*)
    export TURKU3D_BUILD_NICE=$NICE
    exec "$@" ;;
esac
if [ -z "${INVOCATION_ID:-}" ] && command -v systemd-run >/dev/null \
    && systemd-run --user --scope --quiet --collect --slice=tilebuilds.slice true 2>/dev/null; then
  exec systemd-run --user --scope --quiet --collect --slice=tilebuilds.slice -- nice -n "$NICE" "$@"
fi
exec nice -n "$NICE" "$@"
