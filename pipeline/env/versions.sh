#!/usr/bin/env bash
# Print tool + library versions of the local pipeline toolchain; exit 1 if anything is missing.
set -uo pipefail
# Generated outside the repository by bootstrap.sh; unavailable to static analysis.
# shellcheck source=/dev/null
source ~/turku3d/envs/pipeline.env
export PYTHONPATH="${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}/pipeline"
fail=0
v() {
  local name=$1
  shift
  local out
  if out=$("$@" 2>&1 | head -1); then printf '%-16s %s\n' "$name" "$out"; else
    printf '%-16s MISSING (%s)\n' "$name" "$out"
    fail=1
  fi
}
v python python --version
v pdal bash -c 'pdal --version 2>&1 | grep -m1 "^pdal "'
v osmium osmium --version
v gdal gdalinfo --version
v node node --version
v gltf-transform gltf-transform --version
v gltfpack bash -c 'gltfpack -h 2>&1 | head -1 | grep -i gltfpack'
v toktx toktx --version
# Passed as a command to v(), which invokes "$@".
# shellcheck disable=SC2329
roofer_version() (
  scratch=$(mktemp -d)
  trap 'rm -rf "$scratch"' EXIT
  cd "$scratch" || exit 1
  roofer --version
)
v roofer roofer_version
v curl curl --version
python - <<'PY' || fail=1
import importlib
mods = ["numpy", "scipy", "shapely", "pyproj", "geopandas", "pyarrow", "laspy", "lazrs", "pdal",
        "triangle", "xdist", "rasterio", "PIL", "lxml", "osmium", "trimesh", "mapbox_earcut", "yaml", "pytest", "turku3d"]
bad = 0
for m in mods:
    try:
        mod = importlib.import_module(m)
        ver = getattr(mod, "__version__", None) or getattr(mod, "version", None) or "?"
        if callable(ver): ver = ver()
        print(f"py:{m:<14} {ver}")
    except Exception as e:
        print(f"py:{m:<14} MISSING ({e.__class__.__name__}: {e})"); bad = 1
import laspy
print("py:laspy backends", [str(b) for b in laspy.LazBackend.detect_available()])
raise SystemExit(bad)
PY
exit $fail
