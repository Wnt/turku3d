#!/bin/bash
# Mac: change the window's lane counts live, without a restart (the pass and the cities scheduler re-read them).
#   window-lanes.sh TURKU [CITIES]      e.g. window-lanes.sh 48 12
# Raising adds lanes within seconds; lowering only holds new dispatches (running tiles finish). Idle city lanes go to
# Turku by themselves while the city queues are empty (window-run.sh watchdog).
set -euo pipefail
G=${BULK_STATE:-$HOME/turku3d/bulk-cache}
T=${1:?turku lanes}; C=${2:-}
IP=$(python3 -c "import json; print(json.load(open('$G/server.json'))['ip'])")
ssh -o ConnectTimeout=5 "root@$IP" "G=/home/wnt/turku3d-bulk-gen3; echo $T > \$G/lanes-turku.tmp && mv \$G/lanes-turku.tmp \$G/lanes-turku
  ${C:+echo $C > \$G/lanes-cities.tmp && mv \$G/lanes-cities.tmp \$G/lanes-cities}
  echo \"\$(date -u +%FT%TZ) lanes turku=$T cities=${C:-unchanged}\" >> \$G/logs/watchdog.log; echo turku \$(cat \$G/lanes-turku) cities \$(cat \$G/lanes-cities)"
