## Result: IN PROGRESS

The orchestrator accepted Phase 1 in substance and explicitly authorized Phase 2
on pinned pipeline commit `38e57aff` in the 2026-10-28 remote handover. The
original strict outer-tile miss (41.897 s versus 30 s) remains recorded below.

Phase 2 public serving is live. Caddy on loopback 7764 forwards draft routes to
7765 and all other traffic, including WebSockets, to 7700. The forwarder uses
an isolated systemd drop-in, with the original credential untouched. Pages-origin
multiplayer probes passed before switching (nickname ML), through the local
proxy (BR), and after switching the public route (TL). Public health is 200 with
Pages CORS, an unapproved origin is 403, and private status is 404.

The client slice needs Mac landing/publication before its published-URL check.
A headed Air Chrome navigated to the actual published URL and confirmed that
the currently published client has no draft streamer. From that Pages origin,
fetching a remote cached manifest and passing all 16 `.col` files through the
published game's actual physics `checkTile` succeeded in 291–496 ms (280,944
collision bytes). This checks live CORS and decoder compatibility, but is not
a claim of automatic streamed drawing in the still-old published client.
The handover branch and GitHub origin/main have unrelated root histories, so
this host preserves the handed-over branch for the orchestrator to land.

The original prebuild passed 78/84 tiles. Its six degenerate-wall failures were
fixed by backporting the already-landed `_extrude` ring weld from `9ad546a` to
pinned `38e57aff`. The second prebuild passed **84/84** using the service queue.
`~/turku3d-drafts/DEPLOYMENT.json` records the base, backport and checksum;
`tools/remote-drafts/roof-ring.patch` reproduces the isolated backport. Collision
validation is unchanged. The next complete main update replaces this source
backport and should be coordinated with `fix-draft-speed`.

## What changed

- Created `~/turku3d-drafts/` on osgallery-dev with source archive
  `38e57affa9496d2bd51ab8472e80e662773828bb`, an independent Linux x86-64 Python
  environment rebuilt from the repository YAML, locked npm tools and KTX 4.4.2.
- Copied only `raw/{ortho,laz,wfs,osm}` and the generated material library with
  rsync. Final dry-run found no differing input files, only three directory mtimes.
  No street imagery, private data or credentials were copied.
- Added the public bounded queue, restricted CORS, ETags, safe manifest projection,
  private management routes, supervised proxy and Pages draft endpoint. Remote
  badge states and collision-before-draw have synthetic regression coverage.
- Changed prebuild to use the producer queue, preventing concurrent writes to the
  same tile from prebuild workers and public requests.
- Added the dedicated environment launcher, repeatable cold prepared-cache
  benchmark, measured conda lock, JSON evidence and operations README under
  `tools/remote-drafts/`. Included the new Python tooling in gate lint selection.
- Retained all eight build logs and generated tiles remotely, and small logs
  locally under `$TURKU3D_WORK/reports/remote-drafts/`.

| Tile | Single | Four simultaneous |
| --- | ---: | ---: |
| `t0_1_2` | 77.789 s | 36.916 s |
| `t0_0_3` | 50.088 s | 52.937 s |
| `t0_7_7` | **41.897 s** | 36.054 s |
| `t0_8_7` | 21.043 s | 39.017 s |

All builds succeeded; parallel batch wall time was 52.944 s. Both centre tiles
passed 90 s. The failing outer tile spent 32.187 s preparing WFS/roof inputs and
4.138 s packaging visuals. The first single build included shared facade-atlas
creation; OS caches were not flushed. Remaining bulk input transfer overlapped
the initial measurements, adding possible I/O contention. This limitation is
recorded rather than treating the failing result as a deployment pass.

## How to verify

```sh
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
node server/tools/probe.mjs
ssh osgallery-dev 'cat ~/turku3d-drafts/reports/baseline-38e57aff/benchmark.json'
```

Original failing benchmark command (exit **2**, `"passed": false`):

```sh
ssh osgallery-dev '~/turku3d-drafts/repo/tools/remote-drafts/run.sh python scripts/dev/host_slots.py build python tools/remote-drafts/benchmark.py --commit 38e57affa9496d2bd51ab8472e80e662773828bb --root /home/wnt/turku3d-drafts/reports/baseline-38e57aff'
```

Use a fresh report directory to rerun; the harness refuses an existing root.
See `tools/remote-drafts/README.md` for complete setup, update and log commands.
The Pages-origin multiplayer probe returned a valid protocol-v1 welcome before
the benchmark. No browser time was used because the deployment gate failed.

## Gate output

`scripts/dev/gate --fast --base main`: **GREEN, 18.894 s** on this host after
dependency setup (63.538 s). All selected fast checks pass, including the two
in-memory public-boundary tests and ten draft-client tests. A complete uncached
run exercised the stages; unchanged successes were cached on the final run.

This clone has no Mac main ref and GitHub main has unrelated history. Local
`main` is anchored at the handed-over branch's Mac base `624a481` for checks;
no main branch was pushed. The Mac orchestrator still reruns its landing gate.
The host's ShellCheck 0.9 falsely reported an indirectly invoked existing
function as unreachable; using 0.11 resolves it without disabling checks.
The current shfmt required a heredoc `then` placement correction in the existing
multiplayer installer. One existing localhost supervisor test had a transient
restart-count assertion; it passed unchanged. A separate run overlapped CDP's
port with a gate fixture port; the CDP tunnel moved to reserved QA port 7772 and
the final gate is green.

## Follow-ups

After `fix-draft-speed` lands, update the retained remote source to main and
repeat the same single/four-way benchmark after all transfers finish. Both outer
single builds must pass 30 s before deploying. Then implement supervised serving,
bounded public queue/CORS/ETags, the multiplayer-preserving proxy, Pages endpoint
and badge states, prebuild the reachable ring, and verify request-to-drawn timing
from the published Pages URL in sandbox Chrome.
