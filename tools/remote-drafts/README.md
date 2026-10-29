# Remote draft serving

The public draft and multiplayer services run as user `ubuntu` on `oci-drafts`
(`82.70.50.61`, Ubuntu 24.04 ARM64). Pages keeps the same URLs:
`https://turku.lab.madekivi.fi/coarse/` and `wss://turku.lab.madekivi.fi/`.
The outbound forwarder connects to `tunnel.lab.madekivi.fi`; no DNS change,
public application listener or new inbound firewall rule is needed.
Caddy binds `127.0.0.1:7764`, serves hashed `/coarse/*` assets directly from the
published coarse directory, sends mutable metadata to the active Python slot, and everything
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

Multiplayer runs from `~/turku3d-mp/repo/server/`, checksum-synced from the deployed
drafts snapshot by `deploy-drafts`, with runtime
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
systemctl --user status turku-mp-server turku-drafts turku-drafts-proxy turku-forwarder-agent
loginctl show-user ubuntu -p Linger
```

Stop the tunnel first when taking the whole stack offline:

```sh
systemctl --user stop turku-forwarder-agent
systemctl --user stop turku-drafts-proxy turku-drafts turku-mp-server
journalctl --user -u turku-mp-server -u turku-forwarder-agent -n 50 --no-pager
tail -n 50 ~/turku3d-drafts/reports/{service,proxy,prebuild}.log
```

Producer, proxy and prebuild logs append to those files; rotate during a stop or
with logrotate copytruncate. Do not enable request-header or token logging.
Completed hourly scores persist; current-hour scores, sessions and broken poles
are in memory and reset on multiplayer restart. Schedule routine multiplayer
updates at an hour boundary when possible. Draft-only updates do not restart it.

## Idle route warming and player demand

After 120 seconds without game draft requests or multiplayer connections, the
active producer fills all four build slots from a 150 m route-distance walk starting
at the pizzeria. Each route tile and its eight neighbours is completed in full
(two passes) before the next ring. Once the route corridor is done, square rings
expand over the bounded register/service area. Existing full content hashes are
skipped. Navigation metadata is read first; it is cached independently of tiles.
Browser requests and multiplayer connection notifications cancel active idle jobs
through the existing worker-pool preemption path. The producer polls multiplayer
presence as a recovery check and pauses if that health check fails. Status is in
`/coarse/health` under `prebuild`, in the private scheduler status and in
`work/prebuild.json`; service logs record progress transitions. Both the data
volume and boot volume must have 15 GiB free before a new idle job starts.

Rebuildable `.stages`, `.inputs`, `cache/lidar`, `cache/coarse-roofs`, `cache/coarse-area` and `cache/inputs` live in
`/srv/turku3d/fast-cache` on the data volume (symlinked from `work/`). OCI block volume speed scales with size: the
150 GB data volume reads ~80 MB/s, the 47 GB boot volume ~17 MB/s (2026-10-29), so generator I/O must not use the
boot volume. Existing stage entries are compressed losslessly; new entries use fast compression when it saves at
least 5 %. The producer can discard outermost stage entries if space runs low, preserving served tiles.

Public clients send `GET /coarse/demand?client=<random-tab-id>&tiles=<ordered-ids>&urgent=<crossings>&visible=<ids>`
every second. Up to 128 ids accommodate multiple saved starts. The random id lives
only in memory; neither it nor tile lists appear in HTTP logs. No position, heading,
speed, account or save data is transmitted. Leases expire after 12 seconds; a new
list immediately replaces that tab's old list. Other tabs' leases remain valid.
Manifest polls carry `client=`; a delayed poll for a removed id cannot revive it.

Four worker processes handle all passes. Upcoming first passes rank first, then
upgrades on the path/in view, then remaining wanted neighbours. One slot stays
available for fresh crossings while upgrades run. Obsolete queued, input-pending
and upgrade jobs are discarded. Once a foreground full pass starts, it finishes
even if the player turns, leaves its tile list or misses the 12-second lease.
The crossing slot remains reserved between leases as well. Obsolete first-pass
workers are cancelled except around their estimated final two seconds, when
they finish at reduced priority. Idle warming remains preemptible and cancels
its jobs whenever player work is pending. Legacy clients still work through
expiring per-tile requests.

`GET /coarse/<tile>/collision.json?client=<id>` publishes sixteen collision cells
together with a cheap flat-colour basic GLB and road graph. Ground, roads, building
shells, bridges and tunnels are visible before the tile becomes drivable. The
first pass does no texture encoding, facade work or decorative prop generation.
The client validates collision and prepares the model before installing the pair;
legacy collision-only responses remain hidden. Full textures and objects arrive
through `manifest.json`, and upgrades also swap collision and visuals together.
A failed texture pass preserves the previous complete pair. Structural producer
and raw-input identities control freshness independently of facade/texture code;
payload URLs remain content-addressed.
New urgent crossings use the reserved worker instead of interrupting upgrades.
Admission grows with live clients, bounded at 256
jobs, so ten independent paths do not compete for a single 24-job window.

Private `work/coarse/.scheduler.json` records the live queue and worker state once
per second. Structured job completion records in `reports/service.log` contain
tile, pass, elapsed worker seconds and outcome, including cancellation. Public
manifests expose `pass_kind` so first/full availability can be measured.

## Update to a new commit

```sh
scripts/dev/deploy-drafts --commit HEAD
curl -fsS http://127.0.0.1:7764/coarse/health  # includes prebuild progress
```

Source archives are incrementally uploaded to immutable `sources/<commit>`.
The alternate persistent systemd producer slot starts on loopback 7765/7766 in
standby, serving complete cached textured outputs while its builders are paused.
After health succeeds, the source pointer and Caddy upstream change, and SIGUSR1
reloads Caddy without closing its listener. The proxy confirms the new slot before
the old producer stops. Only then does the new slot activate its workers.
The same deploy then checksum-syncs `server/` into `~/turku3d-mp/repo/server/`,
including deletion of obsolete source files. `server/data/` and `node_modules/`
are excluded and preserved; changed package manifests trigger `npm ci`.
Only changed multiplayer files trigger a `turku-mp-server` restart. The existing
two-client presence probe runs against the local proxy afterwards, including on
repeated deployments of the same commit. Probe failures fail the deployment.
The service checkout remains in use; the tunnel process stays running. `ACTIVE_PORT`, `ACTIVE_UNIT` and the slot's
environment persist across reboot. Caddy signal reload follows its
[documented signal contract](https://caddyserver.com/docs/command-line#signals).

The demand journal survives source changes, but abandoned legacy requests no
longer replay forever. Both fragment endpoints serve the newest complete retained full
visuals during regeneration, marked `stale: true`, only with current collision. The
structural producer and input identity must match before retained collision is usable.
Otherwise the response carries `collision_pending: true` and an empty collision map:
retained full (or last first-pass) visuals remain immediately available while current
physics rebuilds. Existing resident physics is kept; new physics and parked actors
are attached only from a validated current fragment. Failed builds never authorize
obsolete collision. `last-full.json` protects the last complete textured generation
until another full pass succeeds. Mutable fragment polling continues every two seconds;
prepared visual replacements swap together and unchanged collision stays resident.
Client entries and arrival reports measure how long stale visuals remain visible.
Health reports
`failure_count` and `failed_tiles`; `prebuild.json` records idle build failures. Existing facade assets and stage caches stay in place.
Requested tiles and the idle route walk revalidate; content hashes select the
changed stages. Inspect a tile's `stage-times.json` for hit/miss evidence. Native
dependency changes still require the ARM64 bootstrap before source deployment.

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
ssh oci-drafts 'systemctl --user stop turku-drafts-proxy turku-drafts'
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
# Check the automatic idle prebuild:
curl -fsS http://127.0.0.1:7764/coarse/health
curl --max-time 1 -fsSI -H 'Origin: https://wnt.github.io' \
  https://turku.lab.madekivi.fi/facades/index.json
# On OCI:
systemctl --user status turku-facade-assets.path turku-facade-assets.service
cat ~/turku3d-drafts/reports/facade-assets.log
```

`deploy-drafts` publishes generator sources independently of the Pages game.
Record its producer switch timestamp and the uninterrupted-serving probe in the task result.

## Draft output retention

The active producer sweeps draft products every minute, skipping locked builds.
Publication and pruning share a permanent per-tile lock. Both public manifests
protect their referenced immutable payloads; replaced manifests protect all their
references for 15 minutes after publication (including rapid first/full upgrades).
The first sweep also protects unknown payloads written in the last 15 minutes,
because older deployments did not record historical manifest references.

Each draft stage keeps its two most recently used successful content keys, under
one namespace lock. Existing stores seed those references from their last two
completed entries. Area stages are scoped by source path. Package intermediates
are tile-local and removed after successful publication or by an idle-tile sweep;
the complete package stage already holds their reusable result. The former shared
`.texture-cache` is removed only after the old producer has stopped and the new
slot has activated. Do not run this migration against an older active producer.
Raw-source lidar/class-6 caches are independent of generator versions; prepared
`.inputs` already replaces older keys per dataset. Raw data, streetlevel caches,
facade records, texture libraries and published shared assets are outside pruning.

To request the same online sweep explicitly after deployment:

```sh
ssh oci-drafts '~/turku3d-drafts/run.sh python -m turku3d.coarse.retention --root /srv/turku3d/work/coarse'
ssh oci-drafts 'df -h /srv/turku3d'
```

## Immutable delivery and collision bundles

The public boundary caches content-addressed GLB, JSON, collision and shared
assets for one year (`immutable`); mutable fragments and health remain revalidated.
Caddy reads hashed assets from `/srv/turku3d/work/coarse` without opening a Python
connection, including assets from a retained generation. Its strict route only
admits public hashed names; manifests, collision metadata and mutable aliases
still pass through producer freshness checks. SHA ETags, CORS, conditional GET,
HEAD and ranges work directly from disk. Missing assets return 404 without
falling back to scheduler work.

The producer admits a 1,024-connection burst and uses persistent HTTP/1.1 with
35-second client timeouts. Caddy retires idle upstream connections after 25
seconds. Concurrent metadata polls share a validated snapshot for at most one
second; atomic metadata publication or scheduler version changes invalidate it
immediately. Source discovery and retained-fragment validation release the
scheduler condition, and HTTP writes never hold admission.

Serving capacity acceptance uses the existing forwarder load binary on native
OCI loopback, three mixed and three cached 10-player runs at 32 parallel fetches
per player. It checks body validation, HTTP/transport failures, cached p95 below
1.5 seconds, CORS, ten live multiplayer sockets and concurrent producer activity.
The operator's temporary testing hold was lifted; run through the owned slot:

```sh
source ../sandbox.env
python3 scripts/dev/host_slots.py check -- python3 scripts/dev/probe_producer_capacity.py --commit COMMIT
```

Each complete fragment advertises a `sorted-col-v1` collision bundle assembled
from its immutable cells by the serving layer, without invoking the generator.
Sixteen files are concatenated by lexicographic cell id and addressed by the
SHA-256 of their bytes. The descriptor cache is bounded to 128 tile versions;
bundle files follow the same lifetime as other immutable tile payloads. The game
still checks every cell in the physics worker. Caddy enables negotiated zstd/gzip
for JSON, collision and GLB, following [its encode directive](https://caddyserver.com/docs/caddyfile/directives/encode).
Use gzip level 1 and zstd fastest: per-transfer default gzip CPU pushed native
10-player cached p95 to 1.75–1.98 seconds despite zero serving errors. Two
isolated level-1 runs measured 1.189–1.203 seconds with identical decoded bytes.
The public TLS/tunnel connection already negotiates HTTP/2; the loopback upstream
is HTTP/1.1 and does not need public HTTP/2 server push or a protocol change.
