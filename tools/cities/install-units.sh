#!/usr/bin/env bash
# Install a city's tile service and preview page as systemd user units on osgallery-dev (idempotent):
#   tools/cities/install-units.sh amsterdam 7980
# city-<city>-tiles: the coarse service (on-demand builds + prefetch, /coarse/health) on 127.0.0.1:PORT (it is
#   localhost only), data root
#   ~/turku3d-local/cities/<city>/data, code = this checkout.
# city-<city>-web:  the city share game build (~/turku3d-local/city/current/dist) on HOST:PORT+1 with /coarse
#   proxied to the city's service (the stock client spawns at local x=168 z=246, inside each sample).
set -euo pipefail
CITY=${1:?city}; PORT=${2:?port}
HOST=${CITY_HOST:-192.168.1.114}
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
WorkingDirectory=$HOME/turku3d-local/city/current/src
ExecStart=/usr/bin/node tools/dev/serve-dist.mjs --public --host $HOST --port $((PORT + 1)) --root $HOME/turku3d-local/city/current/dist --coarse http://127.0.0.1:$PORT
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now "city-$CITY-tiles.service" "city-$CITY-web.service"
systemctl --user --no-pager status "city-$CITY-tiles.service" "city-$CITY-web.service" | grep -E "Active|city-"
