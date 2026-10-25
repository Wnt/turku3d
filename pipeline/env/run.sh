#!/usr/bin/env bash
set -euo pipefail
REPO=${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
if [[ ! -f $HOME/turku3d/envs/pipeline.env ]]; then
  echo 'Run make pipeline-bootstrap first' >&2
  exit 1
fi
source "$HOME/turku3d/envs/pipeline.env"
export PYTHONPATH="$REPO/pipeline${PYTHONPATH:+:$PYTHONPATH}"
exec "$@"
