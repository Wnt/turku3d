#!/usr/bin/env bash
# Fetch the raw inputs of the named region-manifest areas (data-manifests/areas/<name>.yaml) into
# the local-areas root, beyond the coarse prefetch units the bulk copy already holds:
#   * the coarse units (lidar ground, 512 px ortho, WFS shells/streets/water, OSM) the generator
#     still lacks, through the generator's own fetcher (normally none: the bulk copy has them);
#   * classified city lidar (pistepilvi) for roof and bridge-deck refinement;
#   * the building registers (kanta:Rakennus, GIS:Rakennukset) for facade semantics.
# Usage: tools/local-areas/fetch.sh AREAS.json AREA...   (run from the repo copy; resumable)
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
areas=$1
shift
for area in "$@"; do
  "$here/run.sh" python - "$areas" "$area" <<'EOF'
import json, os, subprocess, sys
from pathlib import Path
from turku3d import config as C
from turku3d.coarse.sources import NoCoverage, Sources, Unit, unit_paths

tiles = json.loads(Path(sys.argv[1]).read_text())[sys.argv[2]]
units, nocov = set(), []
s = Sources()
for tile in tiles:
    try:
        units.update(u.key for u in s.missing_for_service(C.VisualTile.parse(tile)))
    except NoCoverage as error:
        nocov.append(str(error))
print(f"{sys.argv[2]}: {len(units)} missing coarse units, {len(nocov)} tiles without coverage")
for line in nocov:
    print("  " + line)
root = C.data_root().resolve()
for unit in sorted(units):
    paths = unit_paths(Unit.parse(unit))
    if any(not Path(os.path.realpath(p)).is_relative_to(root) for p in paths):
        print(f"  {unit}: SKIPPED, would write into the linked bulk copy; fetch it there", flush=True)
        continue
    r = subprocess.run([sys.executable, "-m", "turku3d.coarse", "fetch", unit])
    print(f"  {unit}: exit {r.returncode}", flush=True)
EOF
  "$here/run.sh" python -m turku3d.fetch laz --area "$area" --laz-datasets pistepilvi
  "$here/run.sh" python -m turku3d.fetch wfs --area "$area" --layers kanta:Rakennus GIS:Rakennukset
done
