## Result: BLOCKED

Remote serving and the Pages client implementation are complete and verified.
The remaining acceptance check needs the Mac orchestrator to land this branch
and publish the client. The unmodified published game still has `coarse=null`.
No main branch was pushed. A Pages-origin CDP preview draws remote drafts, but
that preview is explicitly separate from a published-build acceptance pass.
Publication was requested through the handover outbox and the conversation.

The orchestrator explicitly accepted Phase 1 in substance in the 2026-10-28
handover, authorizing Phase 2 on `38e57aff` despite the recorded outer-tile miss.

## What changed

The public route is live at `https://turku.lab.madekivi.fi/coarse/`. A supervised
Caddy on loopback 7764 sends draft requests to loopback 7765 and all other
traffic, including WebSockets, to the existing multiplayer server on 7700.
The forwarder change is an isolated systemd drop-in. Its credential was never
read, printed, copied or committed. User lingering is enabled, and producer and
proxy units are enabled for startup.

The public boundary permits Pages and HTTP localhost CORS, restricts L0 IDs to
configured inclusive Turku bounds, atomically caps active/queued/pending work
at 24 with four build workers, returns 429 with Retry-After when full, and serves
ETags with revalidation. Public manifests omit producer/input/timing internals;
management/status/motion routes are unavailable. Request logs contain only
route class and response code. No IP, query, motion or user-agent is recorded.
Prebuild uses this same queue, avoiding competing writes to a tile's files.

The game selects the HTTPS build-time `VITE_DRAFT_URL` on Pages; localhost keeps
its same-origin service. Its badge shows building, queue-full retry and remote
offline states. A published start remains usable if speculative remote drafts
are offline. Every draft still needs all 16 collision cells accepted by the real
physics `checkTile` before scene insertion.

The producer remains `38e57aff`, with the already-landed `_extrude` ring weld
from `9ad546a` backported to fix six degenerate-wall failures. The ordinary
encoder and browser checks were not weakened. The exact backport is retained
in `tools/remote-drafts/roof-ring.patch`; host `DEPLOYMENT.json` records source
and checksum. The original prebuild passed 78/84; the corrected one passed
**84/84**. Updating the warmup list from the currently published 16 full tiles
added eight cells missing from the handover list; its **76/76** ring also passed.
The persistent cache is under `~/turku3d-drafts/work/coarse/` (about 229 MB before
the last ring expansion).

All dedicated data/tools/cache/source files stay under `~/turku3d-drafts/`.
The initial transfer used an explicit allowlist: `raw/{ortho,laz,wfs,osm}` and
generated reusable materials, with no private/street imagery or credentials.
One initial Vite build used its default output path; the newly created output
was verified by creation timestamps and moved into the task sandbox. Subsequent
builds and checks source the task's isolated environment.

### Phase 1 measurements retained

Source: `38e57affa9496d2bd51ab8472e80e662773828bb`.

| Tile | Single | Four simultaneous |
| --- | ---: | ---: |
| `t0_1_2` | 77.789 s | 36.916 s |
| `t0_0_3` | 50.088 s | 52.937 s |
| `t0_7_7` | 41.897 s | 36.054 s |
| `t0_8_7` | 21.043 s | 39.017 s |

All builds succeeded; parallel batch wall time was 52.944 s. The outer single
41.897 s exceeded the original strict 30 s limit. The first build paid shared
atlas creation; raw transfer overlapped early measurements and OS caches were
not flushed. The original result and limitation are preserved in
`tools/remote-drafts/baseline-38e57aff.json` and the host benchmark logs. The
handover's acceptance exception superseded the original deployment stop.

### Public and browser evidence

Pages-origin multiplayer welcome probes passed before switching (ML), through
the local proxy (BR), after switching (TL), and during final host checks (MW, XV).
Public health returns 200 with the exact Pages CORS origin, other origins get
403, private status gets 404, and a matching manifest ETag gets 304. Public
manifests contain only coarse/tile/visual/collision/roads, with 16 collision cells.

Headed Chrome on the Air navigated to the actual published Pages URL. Its old
client has no draft streamer, but a direct Pages-origin request successfully
fetched the remote manifest and passed all 16 collision buffers (280,944 bytes)
through that published game's real physics worker in 291–496 ms.

A separate CDP preview substituted the reviewed production game bundle at the
same Pages origin while retaining real HTTPS draft requests and published full
tiles. In multiplayer car mode, a drive beyond full terrain drew remote drafts
with no game errors. Eight newly wanted cached tiles took **7.649–39.519 s**
from their first wanted poll to completed scene insertion, including collision,
GLB decode and GPU preparation. Initial startup observations and speculative
prefetch delays are excluded from those eight timings. This is preview evidence,
not a claim that the old published client already streams drafts.

Exact timings and caveats: `tools/remote-drafts/pages-preview.json`. Screenshots,
CDP measurements and the valid trace stay under
`~/turku3d-drafts/agent-work/reports/`: `pages-preview-{menu,drive}.png`,
`t0_2_-1-{top,oblique}.png`, `t0_1_5-{top,oblique}.png`, and
`preview-profile-valid/{trace.json.gz,frames.json,alloc.heapprofile}`. Both
affected real tiles were inspected from top and oblique views after the source
backport. No street-image comparison data was copied to this host. These retain
the chosen coarse producer's existing visual quality; the backport only welds
micrometre roof-ring edges.

The supplied local recorder had two template-string escaping errors; correcting
them in `~/turku3d-drafts/bin/record.mjs` produced a valid 10-second profile:
10 frame samples, median sample p50 31.4 ms, maximum sample p95 35.2 ms and no
long tasks while paused. This is a tooling check, not a driving performance
claim. Browser use was about 17 minutes, below the 40-minute budget. The Air
session and temporary preview server were stopped and their locks released.

## How to verify

On osgallery-dev, from the agent checkout:

```sh
source ~/turku3d-drafts/agent/sandbox.env
scripts/dev/gate --fast --base main
npm --prefix game test -- --run tests/unit/coarse-tiles.test.ts
PYTHONPATH=pipeline .venv/bin/python -m unittest discover -s tools/remote-drafts -p test_public.py
node server/tools/probe.mjs
curl -f -H 'Origin: https://wnt.github.io' https://turku.lab.madekivi.fi/coarse/health
systemctl --user is-active turku-drafts.service turku-drafts-proxy.service
cat ~/turku3d-drafts/work/prebuild.json
```

Operations, source update, cache clearing, rollback and exact unit installation
commands are in `tools/remote-drafts/README.md`. After actual Pages publication,
start the Air with game port 7771 and CDP port 7772, navigate to
`https://wnt.github.io/turku-game/`, and verify remote draft requests and drawn
tiles during a drive. Keep browser accounting below the remaining budget and
always stop the Air session afterward.

## Gate output

`scripts/dev/gate --fast --base main`: **GREEN, 3.352 s** (preceding code-check run: 18.894 s), after initial
dependency setup (63.538 s). All selected fast checks pass, including two
in-memory public-boundary tests and ten draft-client tests. Existing checks were
not weakened. The final result includes cached unchanged stage successes.

This clone lacked the Mac main ref, and GitHub origin/main has unrelated root
history. Local `main` is anchored at the handed-over Mac base `624a481` for
checks; the orchestrator must still rebase/land and rerun its Mac gate. Nothing
was pushed to main. ShellCheck 0.11 fixed the host's 0.9 false unreachable-code
warning without disabling checks; current shfmt required a heredoc `then`
placement correction in the existing multiplayer installer. An existing local
supervisor test's transient count assertion passed unchanged on rerun. A check
that overlapped a fixture port was stopped, CDP moved to QA port 7772, and the
final gate passed. No browser specs or deep tests were added or required.

## Follow-ups

The Mac orchestrator must land/publish this branch, then complete the unmodified
published-URL drive and request-to-drawn recording. The serving service can stay
live while that happens; multiplayer is working. Coordinate the complete main
source update with `fix-draft-speed` when its landed commit is supplied; replace
the source tree, omit the now-integrated ring patch, benchmark for comparison,
and rerun prebuild using the published region list.
