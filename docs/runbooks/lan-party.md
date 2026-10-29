# LAN party, 30 October 2026

Readiness owner: `lan-party-readiness`. Jonni plays on his own machine; probes use
the assigned Mac sandbox and OCI only. Public game:
<https://wnt.github.io/turku-game/game/>. Generator and multiplayer:
<https://turku.lab.madekivi.fi/>.

## Freeze and prewarm

Generator cutoff: **30 October 04:00 Europe/Helsinki (02:00 UTC)** unless the
orchestrator changes it. The host `~/turku3d-drafts/LAN_FREEZE.json` stores the
cutoff and, when captured, the deployed commit, producer hash and service unit.
After cutoff, changed generator deployment is refused before the producer switch.
Client-only Pages fixes remain allowed and require the ten-player smoke below.
Coordinate forward exceptions with the orchestrator; never roll back.

From this task's checkout:

```sh
source ../sandbox.env
python3 scripts/dev/lan_freeze.py arm --freeze-at 2026-10-30T04:00:00+02:00
scripts/dev/codex-task wait-cmd -- python3 scripts/dev/lan_party.py watch
```

Run one watcher only. It sends the 03:30 reminder, records the frozen generator,
reports prewarm start/50%/done, and tests when the mission corridor is warm while
continuing wider coverage afterward. Receipts are in
`$TURKU3D_WORK/reports/lan-party/{freeze,latest,watch-state}.json` and
`coverage.jsonl`. Deadline: 09:00 EET. If warming is incomplete, the watcher starts
the test at 08:45 and reports incomplete coverage explicitly.

The normal scheduler uses at most one spare worker while players are online and
all four workers after 120 seconds without player activity. Order: spawn rings,
**885** mission-corridor tiles (153 destinations,
12 landmarks), then **2,401** tiles in the wider walk. It builds structural
first + textured full passes and skips current full identities. Player demand
and multiplayer presence preempt warming. Do not disconnect players or start a
competing bulk builder. `NoCoverage` tiles are unavailable source area, reported
separately from built coverage and genuine generation failures.

```sh
python3 scripts/dev/lan_party.py snapshot
python3 scripts/dev/lan_freeze.py status
```

The snapshot validates generator, raw-input and facade identities and referenced
payload lengths. Coverage uses the complete planned denominator; unavailable tiles
are never counted as built. ETA is an optimistic four-worker estimate from recent
successful first/full worker means; player interruptions, ring barriers and long
tails add time. Keep **5 GiB free on `/` and 15 GiB on `/srv/turku3d`**. Retention
protects current full/first manifests and their payloads, including `last-full.json`;
do not delete warmed tile directories, shared assets, raw inputs or facade stores.

## Start and verify

Use the repository's SSH identity (the `oci-drafts` alias is not configured here):

```sh
ssh -o IdentitiesOnly=yes -i ~/.ssh/oci_cuttlefish ubuntu@82.70.50.61
# On OCI, use the recorded active slot; do not start the retired turku-drafts unit.
systemctl --user start "$(cat ~/turku3d-drafts/ACTIVE_UNIT)" turku-mp-server turku-drafts-proxy turku-forwarder-agent
df -h / /srv/turku3d
curl --max-time 2 -fsS http://127.0.0.1:7764/coarse/health
curl --max-time 2 -fsS http://127.0.0.1:7700/healthz
tail -n 20 ~/turku3d-drafts/reports/service.log
```

From the Mac, curl smoke stays within five seconds total. Inspect response bodies:

```sh
curl --max-time 2 -fsS https://turku.lab.madekivi.fi/coarse/health
curl --max-time 2 -fsS https://turku.lab.madekivi.fi/healthz
scripts/dev/codex-task checks --last 3
```

The `checks` sentinel line must have `alerts: []`. Generator health must be online,
not draining, with no recent genuine full failures/cancellations, full success
at least 99%, and full p50 at most 10 s. Empty build samples prove availability
only. Inspect `prebuild` progress/failures and `visual_audits` too. Multiplayer
must have `ok: true`; compare rejection/pose counters over the test, not their
lifetime totals. The ten-player receipt reports latency, accepted/rejected poses,
CPU/RAM samples, payload readiness and rendered arrival/frame times.

```sh
# This command owns a hard 600 s wall budget, including host/browser slot waits.
python3 scripts/dev/lan_test.py --output "$TURKU3D_WORK/reports/lan-party/ten-player"
```

It runs `ten-drivers.py` at 50 km/h on ten real directed routes, ten production
multiplayer sockets at 20 Hz, and `probe_tile_arrival.py --warmed-cache`
concurrently. Require zero late/missing crossings, ten surviving sockets, no new
rejections, at least 95% accepted poses, ping p95 <250 ms, full visuals by arrival
+5 s, frame p95 <=33.3 ms / p99 <=50 ms, and at least 110 seconds of overlapping
socket/render measurement. One rendered client measures frames; this is not ten
simultaneous GPU clients. The HTTP drivers check collision/basic visuals; the
shared client yardstick checks textured arrival. Unavailable source tiles remain
a known play-area limit even when every supported tile is warmed.

## If generation lags

Check `prebuild.state`, workers, queue, disk, failures and player presence first.
When players leave, let idle warming resume; raise useful warmed coverage before
inviting all players. More than four CPU workers is not a safe speed fix on this
four-core host. If low disk persists, report it; do not sacrifice warmed output.
For a wedged producer, record health/logs and restart only its recorded active
unit on OCI: `systemctl --user restart "$(cat ~/turku3d-drafts/ACTIVE_UNIT)"`.
This keeps the source and disk cache; multiplayer and the tunnel stay running.
Allow in-flight work to drain. Multiplayer restarts lose current-hour sessions
and scores, so reserve them for an identified multiplayer failure.

Fix forward on an isolated branch, select affected checks and use
`codex-task finish`; do not bypass the freeze for a generator fix. Report red
smoke or test results immediately to the orchestrator with the exact commit and
receipt path. An HTTP 200, old full manifest or partial route warm is insufficient
evidence of LAN readiness.
