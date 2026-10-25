#!/usr/bin/env bash
# Idempotent bootstrap of the remote pipeline toolchain under ~/turku3d (never touches ~/leikkipuistodata).
#   ~/turku3d/tools/bin/micromamba        micromamba (static binary)
#   ~/turku3d/mamba                       MAMBA_ROOT_PREFIX (package cache)
#   ~/turku3d/envs/pipeline               conda-forge env from pipeline/env/environment.yml (+ pip -e pipeline)
#   ~/turku3d/tools/npm                   @gltf-transform/cli + gltfpack (Node 24 from the system)
#   ~/turku3d/tools/roofer                3DBAG roofer v1.0.0 release binary
# Usage (normally via `make pipeline-bootstrap` from the Mac, which rsyncs the repo first):
#   bash ~/turku3d/repo/pipeline/env/bootstrap-remote.sh [--update]
set -euo pipefail
T=~/turku3d
REPO=${REPO:-$T/repo}
ENV=$T/envs/pipeline
MM=$T/tools/bin/micromamba
export MAMBA_ROOT_PREFIX=$T/mamba
export PYTHONNOUSERSITE=1   # the remote ~/.local/lib/python3.12 has its own numpy etc.; never mix it in
GLTF_TRANSFORM_VERSION=${GLTF_TRANSFORM_VERSION:-4.5.1}
GLTFPACK_VERSION=${GLTFPACK_VERSION:-1.3.0}
MESHOPT_VERSION=${MESHOPT_VERSION:-1.3.0}   # npm meshoptimizer (encoder for .col; the game decodes with 1.3.0)
ROOFER_VERSION=v1.0.0
UPDATE=0; [[ "${1:-}" == "--update" ]] && UPDATE=1
mkdir -p $T/tools/bin $T/envs $T/data/{raw,cache,dist}

log() { printf '\n== %s\n' "$*"; }

# 1. micromamba
if [[ ! -x $MM ]]; then
  log "micromamba"
  tmp=$(mktemp -d)
  curl -fsSL -o $tmp/mm.tar.bz2 https://micro.mamba.pm/api/micromamba/linux-64/latest
  tar xjf $tmp/mm.tar.bz2 -C $tmp bin/micromamba
  mv $tmp/bin/micromamba $MM && rm -rf $tmp
fi
$MM --version

# 2. conda env
if [[ ! -x $ENV/bin/python ]]; then
  log "create $ENV"
  $MM create -y -p $ENV -f $REPO/pipeline/env/environment.yml
elif [[ $UPDATE == 1 ]]; then
  log "update $ENV"
  $MM install -y -p $ENV -f $REPO/pipeline/env/environment.yml
fi
log "pip install -e pipeline"
PYTHONNOUSERSITE=1 $ENV/bin/python -m pip install -q --no-deps -e $REPO/pipeline
# pip deps from environment.yml are only installed on create; make sure they are present
$ENV/bin/python -c "import triangle, open3d" 2>/dev/null || $ENV/bin/python -m pip install -q 'triangle>=20230923' 'open3d-cpu>=0.19'
$MM list -p $ENV --explicit > $T/envs/pipeline.lock.txt 2>/dev/null || $MM list -p $ENV > $T/envs/pipeline.lock.txt

# 3. Node tools (system Node 24)
log "node tools"
node --version
NPMDIR=$T/tools/npm
mkdir -p $NPMDIR
if [[ ! -x $NPMDIR/node_modules/.bin/gltf-transform || ! -x $NPMDIR/node_modules/.bin/gltfpack || $UPDATE == 1 \
      || "$(node -p "require('$NPMDIR/node_modules/meshoptimizer/package.json').version" 2>/dev/null)" != "$MESHOPT_VERSION" ]]; then
  (cd $NPMDIR && [[ -f package.json ]] || echo '{"name":"turku3d-tools","private":true}' > $NPMDIR/package.json
   cd $NPMDIR && npm install --no-audit --no-fund --save-exact \
     @gltf-transform/cli@$GLTF_TRANSFORM_VERSION gltfpack@$GLTFPACK_VERSION meshoptimizer@$MESHOPT_VERSION)
fi
ln -sf $NPMDIR/node_modules/.bin/gltf-transform $T/tools/bin/gltf-transform
ln -sf $NPMDIR/node_modules/.bin/gltfpack $T/tools/bin/gltfpack

# 4. roofer release tarball
RD=$T/tools/roofer
if [[ ! -x $RD/bin/roofer ]]; then
  log "roofer $ROOFER_VERSION"
  tmp=$(mktemp -d)
  curl -fsSL -o $tmp/roofer.tgz \
    https://github.com/3DBAG/roofer/releases/download/$ROOFER_VERSION/roofer-linux-x86_64-$ROOFER_VERSION.tar.gz
  mkdir -p $RD && tar xzf $tmp/roofer.tgz -C $RD && rm -rf $tmp
  # some tarballs nest one directory; flatten
  if [[ ! -e $RD/bin/roofer ]]; then inner=$(dirname "$(find $RD -path '*/bin/roofer' | head -1)"); mv "$inner"/../* $RD/; fi
  chmod +x $RD/bin/*   # the tarball ships it non-executable
fi
cat > $T/tools/bin/roofer <<RW
#!/usr/bin/env bash
export GDAL_DATA=$RD/share/gdal PROJ_DATA=$RD/share/proj
exec $RD/bin/roofer "\$@"
RW
chmod +x $T/tools/bin/roofer

# 5. activation snippet
cat > $T/envs/pipeline.env <<ENVSH
# source ~/turku3d/envs/pipeline.env
export PATH=$ENV/bin:$T/tools/bin:\$PATH
export PROJ_DATA=$ENV/share/proj
export GDAL_DATA=$ENV/share/gdal
export TURKU3D_DATA=$T/data
export PYTHONNOUSERSITE=1
# npm meshoptimizer for the .col writer's encoder bridge (turku3d/col/meshopt.py), same version as the game decoder
export TURKU3D_MESHOPT_DIR=$NPMDIR/node_modules/meshoptimizer
ENVSH

log "versions"
bash $REPO/pipeline/env/versions.sh
