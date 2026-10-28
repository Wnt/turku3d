# Remote draft serving

Pages requests drafts from `https://turku.lab.madekivi.fi/coarse/`. The tunnel
passes through Caddy on loopback 7764: `/coarse/*` goes to the bounded draft
service on 7765, and other requests, including multiplayer WebSockets, go to
the existing server on 7700. Both servers remain private loopback listeners.

The initial pipeline stays pinned to `38e57aff`. The orchestrator explicitly
accepted the Phase 1 measurements in substance on 2026-10-28 and authorized
Phase 2, including the recorded 41.897 s outer-tile exception. Keep that
measurement when comparing a later pipeline; it was not a strict timing pass.

All draft data, tools, pipeline sources, cache and logs live under
`~/turku3d-drafts/`. User systemd units and a forwarder drop-in are the only
configuration outside that directory. The forwarder credential stays in its
existing EnvironmentFile and is never read, copied, printed or committed.

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
Rerun after that fix with completed transfers and retain both reports; the
initial serving deployment uses the explicit acceptance exception above.

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
status, restart, drain or cache-clear endpoint. `/coarse/health` returns only
`{"state":"online"}`; tile manifests omit pipeline timings and input internals.


## Install and start serving

Run from the reviewed agent checkout. Copy only the serving directory over the
pinned pipeline tree; copying the whole checkout would change the producer.

```sh
cd ~/turku3d-drafts/agent-repo
rsync -a tools/remote-drafts/ ~/turku3d-drafts/repo/tools/remote-drafts/
mkdir -p ~/.config/systemd/user ~/turku3d-drafts/reports
cp tools/remote-drafts/turku-drafts*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now turku-drafts.service turku-drafts-proxy.service
node server/tools/probe.mjs
node server/tools/probe.mjs ws://127.0.0.1:7764/
```

Only after both probes pass, switch the existing forwarder through a drop-in:

```sh
mkdir -p ~/.config/systemd/user/turku-forwarder-agent.service.d
cp tools/remote-drafts/forwarder-proxy.conf \
  ~/.config/systemd/user/turku-forwarder-agent.service.d/remote-drafts.conf
systemctl --user daemon-reload
systemctl --user restart turku-forwarder-agent.service
node server/tools/probe.mjs
curl -f -H 'Origin: https://wnt.github.io' https://turku.lab.madekivi.fi/coarse/health
```

A failed post-switch WebSocket probe requires immediate rollback:

```sh
rm ~/.config/systemd/user/turku-forwarder-agent.service.d/remote-drafts.conf
systemctl --user daemon-reload
systemctl --user restart turku-forwarder-agent.service
node server/tools/probe.mjs
```

To stop drafts, roll back the tunnel first, then run
`systemctl --user stop turku-drafts-proxy.service turku-drafts.service`.
`systemctl --user is-enabled turku-drafts.service turku-drafts-proxy.service`
checks startup persistence; `loginctl show-user "$USER" -p Linger` checks that
user services survive logout. Proxy and producer run with no new privileges.

## Prebuild and cache

`config.json` limits tile indices inclusively, workers (4) and total admitted
running/queued/pending builds (24). Admission is atomic. Cached tiles bypass the
cap. Invalid tile IDs and private management routes return 404, full queues 429
with Retry-After, and pipeline failures a generic unavailable response. Only
Pages and HTTP localhost origins get CORS. No client motion is accepted or logged.
Request logs contain only route class and response code; Caddy access logs are off.
Successful responses have ETags and require revalidation, so producer updates do
not leave stale browser assets. There is no public cache-clear endpoint.

The persistent cache is `~/turku3d-drafts/work/coarse/`. Prebuild uses the same
HTTP admission queue and four polling jobs; it never writes tiles concurrently
with the producer. It warms the listed playable tiles plus a 3x3 ring, expanded
to whole replacement-parent siblings. The initial list contains 84 tiles.

```sh
systemd-run --user --unit=turku-drafts-prebuild --property=Nice=15 \
  --property=StandardOutput=append:$HOME/turku3d-drafts/reports/prebuild.log \
  --property=StandardError=append:$HOME/turku3d-drafts/reports/prebuild.log \
  "$HOME/turku3d-drafts/repo/tools/remote-drafts/run.sh" \
  python tools/remote-drafts/prebuild.py "$HOME/turku3d-drafts/work/prebuild-tiles.json"
tail -f ~/turku3d-drafts/reports/prebuild.log
cat ~/turku3d-drafts/work/prebuild.json
```

Use `systemctl --user stop turku-drafts-prebuild` before replacing its list.
After a failed transient run, `systemctl --user reset-failed turku-drafts-prebuild`
releases its unit name before the next `systemd-run`.
`prebuild-tiles.json` uses the repository playable-list shape `{"tiles":[...]}`;
sync the published playable list when expanding the Pages region. The bounds
also permit uncached travel farther out; those tiles build on demand.

To clear cached tiles, stop prebuild and the producer, preserve any evidence,
then remove only the desired `work/coarse/t0_i_j` directories. Restart the
producer and prebuild. Stop writers first for whole-cache clearing too; leave
`data/raw/` untouched. Prepared input caches are separate under `work/cache/`.

## Update the pipeline

Coordinate with the Mac orchestrator and `fix-draft-speed`; use its landed main
commit after benchmarking. Build a fresh source tree rather than overlaying
pipeline files. Preserve the serving scripts and independent environment:

```sh
cd ~/turku3d-drafts/agent-repo
SOURCE_COMMIT=<reviewed-commit>
mkdir ../repo-next
git archive "$SOURCE_COMMIT" | tar -x -C ../repo-next
rsync -a tools/remote-drafts/ ../repo-next/tools/remote-drafts/
systemctl --user stop turku-drafts-prebuild turku-drafts.service
mv ../repo ../repo-previous
mv ../repo-next ../repo
printf '%s\n' "$SOURCE_COMMIT" > ../SOURCE_COMMIT
systemctl --user start turku-drafts.service
```

The initial `38e57aff` producer additionally carries the already-landed roof-ring
weld from `9ad546a`, which fixes six degenerate-wall failures in the 84-tile ring.
Apply its retained patch only to that original source before startup:

```sh
patch -p1 -d ~/turku3d-drafts/repo < tools/remote-drafts/roof-ring.patch
```

`~/turku3d-drafts/DEPLOYMENT.json` records base commit, backport function and
source checksum. A main commit containing `9ad546a` already includes the fix;
replace the complete source tree and omit the patch on that update. This is a
source backport, with the ordinary encoder and browser validation unchanged.

Rebuild the dedicated environment first if that commit changes environment
files. Re-run the cold benchmark in a new report directory through host_slots,
then restart prebuild. Old tiles become stale automatically through producer
and input hashes; shared textures use content hashes. Rollback is the same
stop/swap/start using `repo-previous`; keep the previous tree until verification.
Caddy and multiplayer can stay running while the draft producer restarts.

## Client and verification

`game/src/net/config.ts` resolves `VITE_DRAFT_URL` at build time (HTTPS only),
defaulting to the public route for `wnt.github.io`. Localhost always uses
same-origin `/coarse/`. Other hosts have no draft service. No feature flag or
public player-position endpoint is used. The badge shows building, remote queue
full (retrying) and remote offline states. All 16 collision cells must be present
and pass the existing physics `checkTile` before a draft is drawn. Public startup
requires the start tile when it is a draft, while speculative remote neighbours
do not block entry into published terrain during an outage.

```sh
npm --prefix game test -- --run tests/unit/coarse-tiles.test.ts
npm --prefix game run typecheck
PYTHONPATH=pipeline .venv/bin/python -m unittest discover -s tools/remote-drafts -p test_public.py
scripts/dev/gate --fast --base main
node server/tools/probe.mjs
journalctl --user -u turku-drafts.service -u turku-drafts-proxy.service -n 30
tail -f ~/turku3d-drafts/reports/service.log
```

Service stdout/stderr append to `reports/service.log`; proxy diagnostics append
to `reports/proxy.log`. Rotate them during a maintenance stop (or use logrotate
copytruncate). Do not enable request-header logging. Browser evidence and exact
published-URL checks are recorded in `docs/reports/remote-draft-host.md`.
