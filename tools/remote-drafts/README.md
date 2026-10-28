# Remote draft benchmark

The Phase 1 benchmark on osgallery-dev failed the outer-tile limit on 2026-10-28.
No public draft service, proxy, forwarder change, or Pages endpoint was deployed.
The multiplayer WebSocket probe from the Pages origin passed before benchmarking.

Everything created on the host is under `~/turku3d-drafts/`. The existing
`~/turku3d/` and `~/turku3d-mp/` installations remain unchanged. No services were
started; the benchmark exits after its eight builds. Keep the environment and
inputs for a follow-up after `fix-draft-speed` lands.

## Source and measurements

The source archive is commit `38e57affa9496d2bd51ab8472e80e662773828bb`, the newest
service commit in the localhost restart events before `f3c1b5fd` and `10434366`.
Mapping successive `coarse-tiles:` log entries to `coarse`/`starting` events ties
the preceding `b0a6d655` service to `t0_0_2` 40.2 s, `t0_1_2` 41.2 s,
`t0_0_3` 44.6 s and `t0_1_3` 45.8 s. The selected version changes fallback roof
extrusion relative to that predecessor.

| Tile | Single build | Four simultaneous builds | Single limit |
| --- | ---: | ---: | ---: |
| `t0_1_2` | 77.789 s | 36.916 s | 90 s |
| `t0_0_3` | 50.088 s | 52.937 s | 90 s |
| `t0_7_7` | **41.897 s** | 36.054 s | **30 s: FAIL** |
| `t0_8_7` | 21.043 s | 39.017 s | 30 s |

All eight builds exited successfully. The parallel batch took 52.944 s. Each mode
used a separate, initially empty output/input-cache root. Each build started a
fresh Python process, so these are request-to-build CLI timings, including imports;
they do not measure browser drawing. The first tile in each mode also paid for a
shared facade atlas. OS file caches were not flushed. Initial measurements
overlapped the remaining bulk raw-input transfer; the required benchmark inputs
were already present. Host load was 11.98/7.98/6.77 at the start and
10.84/10.40/8.09 at the end. Treat this as a conservative cold-input result, not
an isolated CPU comparison.

The failing outer tile spent 32.187 s in WFS preparation (including lidar roof
refinement) and 4.138 s in visual packaging. It needs at least 11.897 s removed
from its single-build wall time. The speed-fix owner has the measurements.
Rerun after that fix with completed transfers and retain both reports; deployment
requires a passing benchmark first.

The machine-readable stage timings are in [baseline-38e57aff.json](baseline-38e57aff.json).
The original logs, JSON, generated tiles, conda package URLs and pip inventory
remain under `~/turku3d-drafts/reports/` on the host. Small logs and the JSON are
also retained in `$TURKU3D_WORK/reports/remote-drafts/` on the Mac.

## Recreate the dedicated environment

Prerequisites on the host: Linux x86-64, Node 24, npm, curl, tar, rsync and
micromamba. The measured host used micromamba 2.9.0. Run the following from the
Mac sandbox after choosing `SOURCE_COMMIT`. Always transfer an explicit input
allowlist; never transfer the entire raw or data root.

```sh
SOURCE_COMMIT=38e57affa9496d2bd51ab8472e80e662773828bb
git archive "$SOURCE_COMMIT" -o "$TURKU3D_WORK/reports/remote-drafts/source.tar"
ssh osgallery-dev 'mkdir -p ~/turku3d-drafts/{repo,data/raw,tools/bin,envs,work,reports}'
rsync -a "$TURKU3D_WORK/reports/remote-drafts/source.tar" osgallery-dev:turku3d-drafts/source.tar
ssh osgallery-dev 'tar -xf ~/turku3d-drafts/source.tar -C ~/turku3d-drafts/repo'
rsync -a tools/remote-drafts/ osgallery-dev:turku3d-drafts/repo/tools/remote-drafts/
rsync -az "$TURKU3D_DATA/raw/ortho" "$TURKU3D_DATA/raw/laz" \
  "$TURKU3D_DATA/raw/wfs" "$TURKU3D_DATA/raw/osm" osgallery-dev:turku3d-drafts/data/raw/
ssh osgallery-dev 'mkdir -p ~/turku3d-drafts/work/textures/assembly'
rsync -a "$TURKU3D_DATA/live/textures/assembly/library" \
  osgallery-dev:turku3d-drafts/work/textures/assembly/
```

No street imagery, private directory, credentials, browser profiles, or model
weights are required. The archived repository supplies the small manifests,
facade renderer, schemas and prior parameters. The assembled material library
contains generated reusable materials. Raw input directories include their
registration metadata and relative-path VRTs.

The measured conda package URLs are pinned in [linux-64.lock.txt](linux-64.lock.txt).
For an exact conda replay, use that file instead of `environment.yml`, then
install `triangle==20250106` with `run.sh python -m pip install triangle==20250106`
and the `jsonschema` dependency below. The YAML command re-solves the repository's
platform-independent dependency requirements for Linux.

Run on the host, using an already installed micromamba executable to create the
dedicated environment (the measured setup copied only the micromamba executable
into this prefix):

```sh
cd ~/turku3d-drafts
PYTHONNOUSERSITE=1 tools/bin/micromamba create -y -r "$PWD/mamba" \
  -p "$PWD/envs/pipeline" -f repo/pipeline/env/environment.yml
mkdir -p tools/npm
cp repo/pipeline/env/package*.json tools/npm/
(cd tools/npm && npm ci)
curl -fL --retry 2 -o tools/ktx.tar.bz2 \
  https://github.com/KhronosGroup/KTX-Software/releases/download/v4.4.2/KTX-Software-4.4.2-Linux-x86_64.tar.bz2
tar -xf tools/ktx.tar.bz2 -C tools
repo/tools/remote-drafts/run.sh python -m pip install 'jsonschema>=4.23'
repo/tools/remote-drafts/run.sh toktx --version
```

`jsonschema` is the lightweight facade-renderer dependency from
`textures/pyproject.toml`; installing the full texture research environment is
unnecessary. `run.sh` selects only this deployment's data, work directories,
native environment and npm packages. It disables Python user-site imports and
limits native BLAS threads to one per process. It never sources the legacy
host's `pipeline.env`.

## Repeat or update

Wait for rsync to finish before repeating the benchmark. Updating the source
means extracting a new archive into a **new empty source directory**, installing
its environment files if changed, then switching `repo` only while no benchmark
is running. Do not extract over old pipeline files: removed modules would survive.
The deployment's `SOURCE_COMMIT` should identify the selected archive. The initial
benchmark used the full SHA supplied explicitly to the command below.

The output root must not already exist; this prevents accidentally measuring
prepared-cache hits. Run through the host slot scheduler, with a unique report
directory, and retain the complete output. Exit 2 means a timing gate or build
failed. Both near-centre single builds must be at most 90 s and both outer single
builds at most 30 s. All four parallel builds must succeed.

```sh
cd ~/turku3d-drafts
repo/tools/remote-drafts/run.sh python scripts/dev/host_slots.py build \
  python tools/remote-drafts/benchmark.py \
  --commit 38e57affa9496d2bd51ab8472e80e662773828bb \
  --root "$PWD/reports/repeat-38e57aff-$(date +%s)"
```

Inspect `reports/<run>/benchmark.json` and `single-*.log` / `parallel-*.log`.
Benchmark cache clearing means deleting only the chosen run directory after
preserving its JSON/logs, while no benchmark is using it. There is no public
status, restart, drain or cache-clear endpoint. Supervised serving, public queue
and CORS controls, ETags, reverse proxying, Pages client configuration and the
published-browser proof remain follow-up work after the timing gate passes.
