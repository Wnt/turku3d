# Remote draft serving

The public draft and multiplayer services run as user `ubuntu` on `oci-drafts`
(`82.70.50.61`, Ubuntu 24.04 ARM64). Pages keeps the same URLs:
`https://turku.lab.madekivi.fi/coarse/` and `wss://turku.lab.madekivi.fi/`.
The outbound forwarder connects to `tunnel.lab.madekivi.fi`; no DNS change,
public application listener or new inbound firewall rule is needed.
Caddy binds `127.0.0.1:7764`, sends `/coarse/*` to `127.0.0.1:7765`, and everything
else, including WebSockets, to multiplayer on `127.0.0.1:7700`.

The deployed pipeline is current published main `0c43828`, with structural first
passes, forward scheduling and prepared-input caching. The source snapshot also
includes the migration installer; `SOURCE_COMMIT` records its full commit. The old osgallery-dev
`38e57aff` deployment is retained for rollback through **2026-11-04**. Its files
and data must remain intact until then. Its four service units are disabled.
Historical measurements remain in [baseline-38e57aff.json](baseline-38e57aff.json)
and [the original host report](../../docs/reports/remote-draft-host.md).
The migration evidence is in [the OCI report](../../docs/reports/move-to-oci.md).

## Host layout and bootstrap

`~/turku3d-drafts/` contains `repo/`, `envs/`, `tools/`, `reports/`, `run.sh`, and
`SOURCE_COMMIT`. `data` and `work` are symlinks into the existing 150 GB XFS volume
at `/srv/turku3d/{data,work}`. Do not replace these symlinks or build on the boot
disk. Jonni's `HOST-SETUP.json`, `data-volume.json`, `bootstrap-arm64.sh`,
`prepare-and-benchmark.sh` and baseline reports remain on the host.
The user service checks for the mounted data/work paths before starting; linger
is enabled. No cloud resources were created by the migration.

The reusable ARM64 bootstrap uses the explicit repository conda environment,
Node 24.18.0, KTX 4.4.2 ARM64, and Triangle's pinned upstream source commit:

```sh
# On OCI, after extracting the chosen source into ~/turku3d-drafts/repo:
bash ~/turku3d-drafts/repo/tools/remote-drafts/bootstrap-arm64.sh
~/turku3d-drafts/run.sh python -c 'import pdal, rasterio, triangle, jsonschema'
df -h / /srv/turku3d
```

`pipeline/env/linux-aarch64.lock.txt` is Jonni's verified explicit environment.
`run.sh` selects the deployment's native tools, limits BLAS threads and disables
Python user-site imports. Never source osgallery-dev's legacy `pipeline.env`.
Only approved city lidar, city WFS, city ortho and OSM inputs were transferred;
no street imagery, private tree or Google-derived data is needed or copied.

Multiplayer has a separate source snapshot at `~/turku3d-mp/repo/server/`, runtime
data in `server/data/`, and an architecture-neutral binary name
`~/turku3d-mp/bin/forwarder-agent`. The forwarder was compiled from Wnt/forwarder
with its pinned Go compiler and vendored dependencies because CI artifacts were
unavailable and its artifact matrix lacked Linux ARM64. Source and build logs
remain under the draft tools/reports directories. Caddy is the checksum-verified
2.11.7 ARM64 release. Neither application needs root.

## Install, start, stop and logs

The shared installer supports both Linux architectures. Before running it,
provide the source snapshots, native tools, forwarder binary and the existing
credential at `~/.config/turku3d/forwarder.env` (0600). Transfer that credential
only directly host-to-host to the same path, never through a repository or a
report. The installer does not acquire credentials or start a public tunnel.

```sh
~/turku3d-drafts/run.sh bash ~/turku3d-mp/repo/server/deploy/install.sh --drafts
systemctl --user enable --now turku-mp-server turku-drafts turku-drafts-proxy
~/turku3d-drafts/run.sh node ~/turku3d-mp/repo/server/tools/probe-presence.mjs ws://127.0.0.1:7764/
curl -f -H 'Origin: https://wnt.github.io' http://127.0.0.1:7764/coarse/health
# Only after the old host's forwarder has stopped:
systemctl --user enable --now turku-forwarder-agent
systemctl --user enable --now turku-drafts-prebuild
systemctl --user status turku-mp-server turku-drafts turku-drafts-proxy turku-forwarder-agent
loginctl show-user ubuntu -p Linger
```

Stop the tunnel first when taking the whole stack offline:

```sh
systemctl --user stop turku-forwarder-agent
systemctl --user stop turku-drafts-prebuild turku-drafts-proxy turku-drafts turku-mp-server
journalctl --user -u turku-mp-server -u turku-forwarder-agent -n 50 --no-pager
tail -n 50 ~/turku3d-drafts/reports/{service,proxy,prebuild}.log
```

Producer, proxy and prebuild logs append to those files; rotate during a stop or
with logrotate copytruncate. Do not enable request-header or token logging.
Completed hourly scores persist; current-hour scores, sessions and broken poles
are in memory and reset on multiplayer restart. Schedule routine multiplayer
updates at an hour boundary when possible. Draft-only updates do not restart it.

## Prebuild and cache

Refresh the list from the actual Pages manifest, then warm through the bounded
HTTP queue. The supervised oneshot takes the host build slot and starts on boot.
It warms playable tiles and the replacement-parent-expanded 3x3 ring; the
migration manifest contains 92 ring tiles. Structural passes are immediately
servable; the producer upgrades their appearance in the background.

```sh
~/turku3d-drafts/run.sh python - <<'PY'
import json
import urllib.request
from pathlib import Path
m = json.load(urllib.request.urlopen('https://wnt.github.io/turku-game/tiles/manifest.json'))
p = Path.home() / 'turku3d-drafts/work/prebuild-tiles.json'
p.write_text(json.dumps({'tiles': sorted(t for t in m['visual'] if t.startswith('t0_'))}, indent=2) + '\n')
PY
systemctl --user restart --no-block turku-drafts-prebuild
tail -f ~/turku3d-drafts/reports/prebuild.log
cat ~/turku3d-drafts/work/prebuild.json
```

The persistent serving cache is `work/coarse/`; prepared caches are under
`work/cache/` and the coarse output root. To clear a tile, stop prebuild and
producer, preserve needed evidence, remove only the chosen
`~/turku3d-drafts/work/coarse/t0_i_j` directory, then restart producer and prebuild.
Stop writers for whole-cache clearing too. Never delete `data/raw/` or the
symlink targets themselves. There is no public management or cache-clear route.

`config.json` sets inclusive index bounds, four foreground workers and a cap of
24 admitted running/queued/pending requests. Cached requests bypass the cap;
full queues return 429 with Retry-After. Public manifests omit timings and
filesystem details; CORS permits Pages and HTTP localhost. Request logs include
only route class and status. ETags and revalidation prevent stale browser assets.

## Update to a new commit

Archive the orchestrator's chosen main commit into a **fresh** empty source
directory; never overlay pipeline files. Transfer the archive with `git archive`
over SSH from a checkout with that commit. Rebuild the native environment first
if its manifests changed. Preserve data, tools, reports and the volume symlinks.

```sh
# On the source checkout (reviewed commit, not a remote branch name):
git archive <commit> | ssh oci-drafts 'mkdir ~/turku3d-drafts/repo-next; tar -x -C ~/turku3d-drafts/repo-next'
# On OCI, with no other deployment update in progress:
systemctl --user stop turku-drafts-prebuild turku-drafts
mv ~/turku3d-drafts/repo ~/turku3d-drafts/repo-previous
mv ~/turku3d-drafts/repo-next ~/turku3d-drafts/repo
printf '%s\n' '<full-commit>' > ~/turku3d-drafts/SOURCE_COMMIT
systemctl --user start turku-drafts
systemctl --user restart --no-block turku-drafts-prebuild
```

Use a unique backup directory if `repo-previous` already exists. Keep the old
source until verification. Code and input hashes invalidate stale tiles
automatically; omit the old roof-ring patch on main. Update the multiplayer
source separately while it is stopped, preserving `server/data`, run `npm ci
--prefix ~/turku3d-mp/repo/server --ignore-scripts --omit=dev`, and restart it.
Reinstall shared units if they changed; installation itself does not restart them.

## Benchmark and public verification

Use a new benchmark directory each time. The full-build benchmark uses isolated
output roots, fresh Python processes and unflushed OS caches, matching Jonni's
method. The HTTP probe measures manifest availability and downloads the visual
and all 16 collision payloads with the Pages Origin; it does not measure browser
physics installation or render time. Choose an absent tile for a cold request.

```sh
~/turku3d-drafts/run.sh python scripts/dev/host_slots.py build \
  python tools/remote-drafts/benchmark.py --commit "$(cat ~/turku3d-drafts/SOURCE_COMMIT)" \
  --root "$HOME/turku3d-drafts/reports/repeat-$(date +%s)"
~/turku3d-drafts/run.sh node ~/turku3d-mp/repo/server/tools/probe-presence.mjs
~/turku3d-drafts/run.sh python tools/remote-drafts/probe.py t0_1_2
curl -f -H 'Origin: https://wnt.github.io' https://turku.lab.madekivi.fi/coarse/health
sudo ss -ltnp
```

## Host cutover and rollback

`cutover.sh <destination> <uncached-tile>` runs on the source host after local
checks and ring warmup. It stops the source tunnel and multiplayer (freezing
writes), stops destination multiplayer, rsyncs `server/data` last, then starts
destination multiplayer and its tunnel. It verifies health, two-client presence,
a cached tile and an uncached build with the Pages Origin. Any failure stops the
destination tunnel and restores source services automatically. Success disables
the source's four units. Exactly one tunnel agent runs at a time.

To roll back after a completed migration, run from osgallery-dev. The source
uses its retained amd64 units and original forwarder drop-in:

```sh
ssh oci-drafts 'systemctl --user disable --now turku-forwarder-agent; systemctl --user stop turku-mp-server'
rsync -a oci-drafts:turku3d-mp/repo/server/data/ ~/turku3d-mp/repo/server/data/
systemctl --user enable --now turku-mp-server turku-drafts turku-drafts-proxy turku-forwarder-agent
node ~/turku3d-mp/repo/server/tools/probe.mjs
curl -f -H 'Origin: https://wnt.github.io' https://turku.lab.madekivi.fi/coarse/health
ssh oci-drafts 'systemctl --user stop turku-drafts-prebuild turku-drafts-proxy turku-drafts'
```

For a pipeline-only rollback, stop producer/prebuild, swap back the retained
source directory, update `SOURCE_COMMIT` and restart them. The tunnel and
multiplayer can continue running throughout.

## Existing facade assets

Drafts retain existing generated facade maps by permanent building id. The public
runtime store at `https://turku.lab.madekivi.fi/facades/index.json` contains only
validated generated maps, their wall geometry, descriptions and the material
library. Reference imagery, model job records and private paths stay outside that
route. New AI facade generation remains paused; serving and warming drafts never
starts description or image-generation jobs.

The orchestrator mirrors existing `facade-params/`, `facade-approvals/` and
`textures/` into `/srv/turku3d/data`. The `turku-facade-assets.path` unit watches
those stores and exports changed runtime files only. It retries while a source
mirror is incomplete. Localhost reads the same approved texture assembly directly;
a sandbox's own parameter record overrides a shared record. Draft invalidation
hashes only the facade records of the buildings in that tile. Matched surveyed
walls retain the original albedo, normal, ORM and metre UV transforms; unmatched
walls retain the draft facade kit.

```sh
# Refresh the OCI runtime export from the already mirrored data (no data upload):
scripts/dev/publish-facades
# Incremental generator source upload, restart and metrics (no warming wait):
scripts/dev/deploy-drafts --commit HEAD
# Recovery: re-enqueue nearest-first warming asynchronously (deploy already starts it):
scripts/dev/deploy-drafts --prewarm
curl --max-time 1 -fsSI -H 'Origin: https://wnt.github.io' \
  https://turku.lab.madekivi.fi/facades/index.json
# On OCI:
systemctl --user status turku-facade-assets.path turku-facade-assets.service
cat ~/turku3d-drafts/reports/facade-assets.log
```

`deploy-drafts` archives the specified commit, rsyncs only changed source files
using the previous source as a hard-link basis, validates Caddy, then switches the
source pointer and restarts the draft producer/proxy. The draft service journals
queued, building and pending demand under its persistent coarse root and replays it
before accepting new work. Deployment immediately starts nearest-first ring warming
with every configured foreground worker. Multiplayer and its tunnel keep running. Native dependency changes still require the ARM64 bootstrap before
this source-only deployment. `--prewarm` starts the supervised ring warmer and
returns immediately; cached manifests are checked against the current generator.
Record public producer/proxy restart times in the task result. Never restart a producer
version that cannot preserve queued demand during normal deployment.
