#!/usr/bin/env bash
# Run ON a non-root bulk host that can SSH to OCI itself (e.g. osgallery-dev with ~/.ssh/oci_cuttlefish):
# pulls OCI's deployed source, inputs and caches into BULK_ROOT, installs the x86 pipeline env and
# mirrors OCI's absolute paths with sudo symlinks. Never touches an existing ~/turku3d-drafts.
set -euo pipefail
BULK_ROOT=${BULK_ROOT:-$HOME/turku3d-bulk-warmup}
OCI=${OCI:-ubuntu@82.70.50.61}
KEY=${OCI_KEY:-$HOME/.ssh/oci_cuttlefish}
E="ssh -c aes128-gcm@openssh.com -i $KEY -o IdentitiesOnly=yes"
mkdir -p "$BULK_ROOT"/{repo,data,work/cache,work/coarse,state,tools/bin,tools/npm,envs}
cd "$BULK_ROOT"
rsync -a -e "$E" "$OCI:turku3d-drafts/repo/" repo/
# Inputs and caches in parallel; OCI's data volume (~80 MB/s) is the limit.
pids=()
rsync -a -e "$E" "$OCI:/srv/turku3d/data/raw/" data/raw/ & pids+=($!)
rsync -a -e "$E" --exclude=raw --exclude=cache/streetlevel "$OCI:/srv/turku3d/data/" data/ & pids+=($!)
rsync -a -e "$E" "$OCI:/srv/turku3d/fast-cache/cache/" work/cache/ & pids+=($!)
rsync -a -e "$E" "$OCI:/srv/turku3d/fast-cache/coarse/.inputs/" work/coarse/.inputs/ & pids+=($!)
rsync -a -e "$E" "$OCI:/srv/turku3d/work/coarse/shared" "$OCI:/srv/turku3d/work/coarse/navigation-*.json" work/coarse/ & pids+=($!)
rsync -a -e "$E" "$OCI:/srv/turku3d/work/cache/vegetation" work/cache/ & pids+=($!)
rsync -a -e "$E" "$OCI:/srv/turku3d/work/facade-rules-models" "$OCI:/srv/turku3d/work/prebuild-tiles.json" work/ & pids+=($!)
for p in "${pids[@]}"; do wait "$p"; done
target=$($E "$OCI" readlink /srv/turku3d/work/facade-rules.json)
ln -sfn "$BULK_ROOT/work/facade-rules-models/$(basename "$target")" work/facade-rules.json
# x86 pipeline environment (same lock and tools as bootstrap-x86.sh).
if [ ! -x tools/bin/micromamba ]; then
  curl -fsL --retry 3 https://micro.mamba.pm/api/micromamba/linux-64/2.9.0 -o tools/micromamba.tar.bz2
  tar -xjf tools/micromamba.tar.bz2 -C tools bin/micromamba
fi
if [ ! -x tools/node-v24.18.0-linux-x64/bin/node ]; then
  curl -fsL --retry 3 https://nodejs.org/dist/v24.18.0/node-v24.18.0-linux-x64.tar.xz -o tools/node.tar.xz
  tar -xJf tools/node.tar.xz -C tools
fi
if [ ! -x tools/KTX-Software-4.4.2-Linux-x86_64/bin/toktx ]; then
  curl -fsL --retry 3 https://github.com/KhronosGroup/KTX-Software/releases/download/v4.4.2/KTX-Software-4.4.2-Linux-x86_64.tar.bz2 -o tools/ktx.tar.bz2
  printf '%s\n' 'a8781bad05f9624edbf910b7f258cd0a4ba7d3e63b49ecc0a0ab440bf6a0a245  tools/ktx.tar.bz2' | sha256sum -c -
  tar -xjf tools/ktx.tar.bz2 -C tools
fi
if [ ! -x envs/pipeline/bin/python ]; then
  PYTHONNOUSERSITE=1 tools/bin/micromamba create -q -y -r "$BULK_ROOT/mamba" -p "$BULK_ROOT/envs/pipeline" -f repo/tools/remote-drafts/linux-64.lock.txt
fi
envs/pipeline/bin/python -m pip install -q triangle==20250106 jsonschema==4.26.0
cp repo/pipeline/env/package*.json tools/npm/
(cd tools/npm && PATH="$BULK_ROOT/tools/node-v24.18.0-linux-x64/bin:$PATH" npm ci --silent)
# OCI's absolute paths (stage caches and the input-hash index store them).
sudo mkdir -p /home/ubuntu /srv/turku3d/fast-cache
sudo ln -sfn "$BULK_ROOT" /home/ubuntu/turku3d-drafts
sudo ln -sfn "$BULK_ROOT/work" /srv/turku3d/work
sudo ln -sfn "$BULK_ROOT/data" /srv/turku3d/data
sudo ln -sfn "$BULK_ROOT/work/cache" /srv/turku3d/fast-cache/cache
sudo ln -sfn "$BULK_ROOT/work/coarse" /srv/turku3d/fast-cache/coarse
TURKU3D_DRAFT_HOME=/home/ubuntu/turku3d-drafts repo/tools/remote-drafts/run.sh python -c \
  'from turku3d.coarse.packaging import current_code; print("generator", current_code())'
