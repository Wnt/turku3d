# LAN warm-up, 30 October 2026

Jonni's own OCI playtests are the primary LAN readiness evidence. This agent owns
cache warm-up, the generator freeze and coverage reporting.

Generator freeze: **30 October 04:00 Europe/Helsinki (02:00 UTC)** unless the
orchestrator changes it. The host `~/turku3d-drafts/LAN_FREEZE.json` records the
cutoff, then the frozen commit, producer hash and active unit. Changed generator
deploys are refused after cutoff; fix forward with the orchestrator. No rollbacks.

The normal scheduler yields to player demand. With players present, at most one
spare worker warms fully prepared inputs and any queued player work preempts it.
After 120 seconds without players it uses all four workers: spawn rings, the
**885-tile mission/delivery corridor** (153 sites, 12 landmarks), then the wider
**2,401-tile walk**. Recent outer probes follow that walk when fully idle.
NoCoverage is unavailable source area, not a build failure; its retries wait for
a changed generator/input identity. Do not disconnect players or start a bulk builder.

Rebuildable caches live at **`/srv/turku3d/fast-cache`**. Keep the existing symlinks
for `.stages`, `.inputs`, lidar, coarse-roofs, coarse-area and input caches intact;
**do not move them back to the boot disk**. Keep at least 5 GiB free on `/` and
15 GiB on the data volume. Low disk pauses warming; it must not delete warmed
stages to chase boot space. Current manifests and last-full payloads stay protected.

From the assigned checkout, run one observer only:

```sh
source ../sandbox.env
scripts/dev/codex-task wait-cmd -- python3 scripts/dev/lan_party.py watch
```

It sends the 03:30 freeze reminder, captures the frozen
commit at 04:00, and reports warm-up start/50%/done or incomplete coverage by09:00.
Receipts: `$TURKU3D_WORK/reports/lan-party/{freeze,latest,watch-state}.json` and
`coverage.jsonl`. Coverage checks current generator/input/facade identities and
payload lengths. Unsupported tiles remain separate from built coverage. ETA uses
recent worker means, then observed frozen-cache growth; player interruptions count.

Inspect operational warm-up health on OCI:

```sh
curl --max-time 2 -fsS http://127.0.0.1:7764/coarse/health
curl --max-time 2 -fsS http://127.0.0.1:7700/healthz
free -h
swapon --show --bytes
df -h / /srv/turku3d
```

Read `prebuild` mode/capacity/cached/route_cached/unavailable/failed and rolling
full outcomes, p50 and error kinds. HTTP200 alone is insufficient; full failures,
cancellations or p50>10s keep health red. `codex-task checks --last 3` exposes the
sentinel alert line; empty samples prove availability only. Multiplayer needs
`ok:true`. OCI has 2 GiB zstd zram; swap above about1GiB means too many workers.

If generation lags, let player demand win and check queue, inputs, disk and errors.
Report blockers to the orchestrator. For an identified wedged producer, record logs
and restart only `systemctl --user restart "$(cat ~/turku3d-drafts/ACTIVE_UNIT)"`.
Keep multiplayer/tunnel running; multiplayer restart loses current-hour sessions.
Do not bypass the freeze, prune warmed outputs or claim LAN acceptance from warm-up.
