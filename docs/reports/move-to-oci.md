# OCI draft and multiplayer migration, 2026-10-28

Cutover completed at approximately **17:38:49 UTC**, from osgallery-dev to
`ubuntu@82.70.50.61` (`ssh oci-drafts`). The existing outbound tunnel, public URLs
and DNS are unchanged. Services bind only loopback. Source services are stopped
and disabled; their units, application trees and score data remain available
through 2026-11-04. Other source services were left running.

## Source and environment

The measured current-main snapshot was `c5d0fb6` (full identity in the host's
`SOURCE_COMMIT` and deployment record). It contains structural first passes,
forward scheduling and prepared-input caching. A final refresh to main
`0c43828` includes the host-slot arrival-order fix; its draft pipeline, package
code, textures and multiplayer sources are byte-identical to the measured tree.

Reused Jonni's existing ARM64 toolchain and XFS volume setup. Added his explicit
conda lock to `pipeline/env/linux-aarch64.lock.txt` and a reusable bootstrap at
`tools/remote-drafts/bootstrap-arm64.sh`. Native versions: Python 3.12.16,
Node 24.18.0, KTX 4.4.2, Triangle pinned to
`595b43eb6682992a0b1012b9671bf860c0e6ae56`.

The Wnt/forwarder CI download returned an Azure egress-limit 503. Its existing
artifact matrix also omitted Linux ARM64, so the native agent was built from
`627131beeeaf4838cd55391cc582fe5a638d3346` with Go 1.26.5 and vendored modules.
The Go archive was verified against the official release metadata. Caddy 2.11.7
ARM64 was verified against its release's SHA-512 checksums. Build source and
logs are retained on OCI; no new credentials were acquired.

The existing forwarder credential was copied directly from the canonical source
path to the same destination path, mode 0600. No credentials entered repository
files or reports. Missing city lidar and city WFS inputs were transferred with
rsync; existing city ortho and OSM inputs were reused. No street imagery, private
tree or Google-derived data was transferred.

The pre-existing rpcbind TCP/UDP listener was disabled and masked on OCI. Final
`ss -ltnp` showed only SSH on public addresses and the three application ports
on loopback. No inbound ports or firewall rules were opened. Existing data
volume: 132 GB free; boot volume: 37 GB free after installation.

## Measurements

Same benchmark tiles and isolated output directories as Jonni's run; fresh Python
processes, no OS-cache flush. This is the full appearance pass CLI benchmark,
separate from structural HTTP availability.

| Tile | Jonni's OCI `4420106` single | Current main single | Current main four-way |
| --- | ---: | ---: | ---: |
| `t0_1_2` | 43.954 s | 36.381 s | 27.780 s |
| `t0_0_3` | 31.441 s | 21.969 s | 31.225 s |
| `t0_7_7` | 26.526 s | 11.496 s | 25.431 s |
| `t0_8_7` | 10.504 s | 9.823 s | 24.835 s |

All eight builds passed; four-way batch: **31.230 s**. ARM64 forwarder
compilation overlapped part of that batch, so its concurrency numbers are
conservative. Singles preceded that compilation. Numbers were sent to the
orchestrator inbox before cutover.

Cold near-centre HTTP request (`t0_1_2`, empty serving cache, no local prepared
cache in that output root): structural manifest **4.722 s**, visual and all
16 collision payloads downloaded by **4.743 s**. Inputs already present in the
raw cache; OS caches warm from full-build measurements. This measures backend
edge availability, not browser physics installation or first rendered frame.

The current Pages manifest's replacement-parent-expanded ring contains **92**
tiles. All 92 prebuild requests succeeded before cutover. Structural tiles remain
playable cache hits; appearance upgrades run as requested without blocking them.

## Cutover checks

Stopped the source tunnel before starting its replacement. Stopped both
multiplayer writers, rsynced the final `server/data`, started destination
multiplayer and tunnel, then verified through the public URL with the Pages
Origin (`https://wnt.github.io`):

- `/coarse/health` returned online with allowed-origin CORS.
- Two simultaneous WebSockets completed hello; the second roster contained both
  clients. Session tokens were never logged.
- Cached `t0_1_2`: manifest 0.173 s; visual and all collision payloads by 0.880 s.
- Previously absent `t0_8_7`: manifest 1.773 s; all 17 payloads by 2.422 s.
- Final source/destination score-file checksum comparison reported no differences.
- All four OCI services active/enabled, prebuild oneshot enabled, linger enabled.
- All four source services inactive/disabled, including the restored regular
  draft unit file (systemctl disable had removed its original linked unit).

Current-hour scores and sessions reset on multiplayer restart, as in the
existing server contract; completed-hour files were preserved. The automated
cutover script restores the old host on any verification failure. It did not
need to roll back. No browser suite or geometry algorithm change was involved.

## Retained evidence and operations

OCI:

- `~/turku3d-drafts/reports/main-c5d0fb6/{benchmark.json,*.log}`
- `~/turku3d-drafts/reports/edge-open-c5d0fb6.log`
- `~/turku3d-drafts/work/prebuild.json`, `reports/prebuild.log`
- `~/turku3d-drafts/reports/forwarder-build.log`
- `~/turku3d-drafts/DEPLOYMENT.json`, `SOURCE_COMMIT`

Source host:

- `~/turku3d-drafts/oci-agent/{benchmark.json,cutover.log,gate.log,result.md}`
- `~/turku3d-drafts/oci-agent/inbox.md` contains pre-cutover and cutover reports.

[Operations](../../tools/remote-drafts/README.md) includes start, stop, update,
cache clearing, logs, prebuild and rollback commands. Source rollback retention
ends 2026-11-04; removal is a separate operator action after that date.
