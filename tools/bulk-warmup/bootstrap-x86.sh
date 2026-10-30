#!/usr/bin/env bash
# Run as root on a fresh Ubuntu 24.04 x86_64 bulk host. Installs the pipeline environment into
# /root/turku3d-bulk-warmup and mirrors OCI's absolute paths (stage caches and the input-hash
# index store them). The source tree must already be copied to /root/turku3d-bulk-warmup/repo.
set -euo pipefail
bulk_root=/root/turku3d-bulk-warmup
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq rsync curl bzip2 xz-utils build-essential htop btop iotop sysstat >/dev/null
mkdir -p "$bulk_root"/{envs,tools/bin,tools/npm,data,work/cache,work/coarse,state,reports}
cd "$bulk_root"
if [ ! -x tools/bin/micromamba ]; then
  curl -fL --retry 3 https://micro.mamba.pm/api/micromamba/linux-64/2.9.0 -o tools/micromamba.tar.bz2
  tar -xjf tools/micromamba.tar.bz2 -C tools bin/micromamba
fi
if [ ! -x tools/node-v24.18.0-linux-x64/bin/node ]; then
  curl -fL --retry 3 https://nodejs.org/dist/v24.18.0/node-v24.18.0-linux-x64.tar.xz -o tools/node-v24.18.0-linux-x64.tar.xz
  curl -fL --retry 3 https://nodejs.org/dist/v24.18.0/SHASUMS256.txt -o tools/node-shasums.txt
  (cd tools && grep ' node-v24.18.0-linux-x64.tar.xz$' node-shasums.txt | sha256sum -c -)
  tar -xJf tools/node-v24.18.0-linux-x64.tar.xz -C tools
fi
if [ ! -x tools/KTX-Software-4.4.2-Linux-x86_64/bin/toktx ]; then
  curl -fL --retry 3 https://github.com/KhronosGroup/KTX-Software/releases/download/v4.4.2/KTX-Software-4.4.2-Linux-x86_64.tar.bz2 -o tools/ktx.tar.bz2
  printf '%s\n' 'a8781bad05f9624edbf910b7f258cd0a4ba7d3e63b49ecc0a0ab440bf6a0a245  tools/ktx.tar.bz2' | sha256sum -c -
  tar -xjf tools/ktx.tar.bz2 -C tools
fi
if [ ! -x envs/pipeline/bin/python ]; then
  PYTHONNOUSERSITE=1 tools/bin/micromamba create -y -r "$bulk_root/mamba" -p "$bulk_root/envs/pipeline" -f repo/tools/remote-drafts/linux-64.lock.txt
fi
envs/pipeline/bin/python -m pip install -q triangle==20250106 jsonschema==4.26.0
export PATH="$bulk_root/tools/node-v24.18.0-linux-x64/bin:$PATH"
cp repo/pipeline/env/package*.json tools/npm/
(cd tools/npm && npm ci --silent)
# OCI's absolute paths, so cached stage outputs and input fingerprints resolve here too.
chmod 755 /root
mkdir -p /home/ubuntu /srv/turku3d/fast-cache
ln -sfn "$bulk_root" /home/ubuntu/turku3d-drafts
ln -sfn "$bulk_root/work" /srv/turku3d/work
ln -sfn "$bulk_root/data" /srv/turku3d/data
ln -sfn "$bulk_root/work/cache" /srv/turku3d/fast-cache/cache
ln -sfn "$bulk_root/work/coarse" /srv/turku3d/fast-cache/coarse
"$bulk_root/envs/pipeline/bin/python" -c 'import pdal,rasterio,shapely,triangle,jsonschema; print("Pipeline environment ready")'
