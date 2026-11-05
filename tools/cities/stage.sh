#!/usr/bin/env bash
# Stage a city's cached raw sources from the home-lab bulk pool to a generation host, then set its root up:
#   tools/cities/stage.sh espoo root@upcloud-host [/srv/turku3d/cities]
# Run on osgallery-dev (it holds /bulk/turku3d/cities). The cache is filled by tools/cities/fetch.py; the city
# servers (WFS/WMS ~2-3 MB/s per request) are far slower than the lab upload, so big runs stage from here.
set -euo pipefail
CITY=${1:?city}; HOST=${2:?user@host}; DEST=${3:-/srv/turku3d/cities}
SRC=/bulk/turku3d/cities/$CITY/raw
du -sh --apparent-size "$SRC"
ssh "$HOST" mkdir -p "$DEST/$CITY/raw"
rsync -a --info=progress2 "$SRC/" "$HOST:$DEST/$CITY/raw/"
echo "on $HOST: TURKU3D_CITY_RAW_ROOT=$DEST TURKU3D_CITIES_ROOT=$DEST tools/cities/setup-root.sh $CITY"
