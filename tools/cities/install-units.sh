#!/usr/bin/env bash
# Install a city's tile service and preview page as systemd user units on osgallery-dev (idempotent):
#   tools/cities/install-units.sh amsterdam 7980
# city-<city>-tiles: the coarse service (on-demand builds + prefetch, /coarse/health) on 127.0.0.1:PORT (it is
#   localhost only), data root
#   ~/turku3d-local/cities/<city>/data, code = this checkout.
# city-<city>-web:  a game build (WEB_DIST) on HOST:PORT+1 with /coarse proxied to the city's service; open it
#   with ?city=<city> (game/src/core/city.ts) for the city's frame and centre spawn.
set -euo pipefail
CITY=${1:?city}; PORT=${2:?port}
HOST=${CITY_HOST:-192.168.1.114}
# The client build to serve: WEB_SRC holds tools/dev/serve-dist.mjs, WEB_DIST the vite dist (default: the city share).
WEB_SRC=${WEB_SRC:-$HOME/turku3d-local/city/current/src}
WEB_DIST=${WEB_DIST:-$HOME/turku3d-local/city/current/dist}
# /cities/<name>/coarse/* routing of the client's city switch: {"<city>": "http://127.0.0.1:PORT", ...}
CITIES_FILE=${CITIES_FILE:-$HOME/turku3d-local/cities/preview-cities.json}
[ -f "$CITIES_FILE" ] || echo '{}' > "$CITIES_FILE"
python3 - "$CITIES_FILE" "$CITY" "$PORT" <<'PY'
import json, sys
path, city, port = sys.argv[1:]
data = json.load(open(path))
data[city] = f"http://127.0.0.1:{port}"
open(path, "w").write(json.dumps(data, indent=1, sort_keys=True) + "\n")
PY
REPO=$(cd "$(dirname "$0")/../.." && pwd)
UNITS=$HOME/.config/systemd/user
mkdir -p "$UNITS" "$HOME/turku3d-local/cities/$CITY/logs"
cat > "$UNITS/city-$CITY-tiles.service" <<EOF
[Unit]
Description=Turku3D $CITY coarse tile service on 127.0.0.1:$PORT ($REPO)

[Service]
Type=simple
Environment=RAYON_NUM_THREADS=2 NICE=12
ExecStart=$REPO/tools/cities/run.sh $CITY python -m turku3d.coarse serve --host 127.0.0.1 --port $PORT
StandardOutput=append:$HOME/turku3d-local/cities/$CITY/logs/tiles.log
StandardError=append:$HOME/turku3d-local/cities/$CITY/logs/tiles.log
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
cat > "$UNITS/city-$CITY-web.service" <<EOF
[Unit]
Description=Turku3D $CITY preview page on $HOST:$((PORT + 1)) -> tiles :$PORT

[Service]
Type=simple
Environment=NODE_ENV=production TURKU3D_WORK=$HOME/turku3d-local/cities/$CITY/web
WorkingDirectory=$WEB_SRC
ExecStart=/usr/bin/node tools/dev/serve-dist.mjs --public --host $HOST --port $((PORT + 1)) --root $WEB_DIST --coarse http://127.0.0.1:$PORT --cities $CITIES_FILE
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now "city-$CITY-tiles.service" "city-$CITY-web.service"
systemctl --user --no-pager status "city-$CITY-tiles.service" "city-$CITY-web.service" | grep -E "Active|city-"
