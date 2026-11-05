#!/usr/bin/env bash
# Create a city's data/work roots (idempotent):  tools/cities/setup-root.sh espoo
# raw/ lives on the bulk HDD pool (/bulk/turku3d/cities/<city>/raw); the shared texture and facade
# libraries are city-independent and linked read-only from the toolchain's data root.
set -euo pipefail
CITY=${1:?city}
CITIES=${TURKU3D_CITIES_ROOT:-$HOME/turku3d-local/cities}
BULK=${TURKU3D_CITY_RAW_ROOT:-/bulk/turku3d/cities}
LIBS=${TURKU3D_LIBRARY_ROOT:-$HOME/turku3d-bulk-warmup/data}
D="$CITIES/$CITY/data"
mkdir -p "$D/cache" "$CITIES/$CITY/work" "$BULK/$CITY/raw"
[ -e "$D/raw" ] || ln -s "$BULK/$CITY/raw" "$D/raw"
for lib in textures facade-assets facade-params; do
  [ -e "$D/$lib" ] || [ ! -e "$LIBS/$lib" ] || ln -s "$LIBS/$lib" "$D/$lib"
done
printf '{"city": "%s"}\n' "$CITY" > "$D/city.json"
ls -la "$D"
