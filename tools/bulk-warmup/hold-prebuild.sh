#!/usr/bin/env bash
# Run on OCI as ubuntu: keep the idle prebuild in one-worker spare mode (it believes a player is
# present) while the bulk copy reads the data volume. Stop with: rm ~/hold-prebuild.on
touch ~/hold-prebuild.on
while [ -f ~/hold-prebuild.on ]; do
  curl -s -X POST "http://127.0.0.1:$(cat ~/turku3d-drafts/ACTIVE_PORT)/internal/player-connected" >/dev/null
  sleep 20
done
