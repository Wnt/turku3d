# Host slot scheduler verification (2026-10-26)

Commit `8901516` adds host-wide `build` (3), `browser` (2), and exclusive priority
`land` admission. Build children receive at most two `TURKU3D_JOBS`; Chrome and
landing children receive one. Synthetic process tests verified capacity, landing
priority, dead-PID reclamation, inherited landing reservations, and worker caps.
`codex-task status tool-host-slots` and `watch --plain --once` both displayed
`waiting for land slot` during the measurement.

The full gate passed in **478.58 seconds** after sourcing this sandbox's
`sandbox.env` and setting `TURKU3D_JOBS=2`. Its game and tile Chrome suites passed.
The gate JSON is in `$TURKU3D_WORK/reports/tool-host-gate-sandbox.json`.

For a real tile Chrome probe, the published `TURKU3D_DATA/dist/tiles` tree was
APFS-cloned into this sandbox, and the sandbox game build was used. Two synthetic
CPU build jobs were admitted before a `land` reservation queued. Both jobs
finished after **15.027 seconds**; landing then ran the full tile Playwright
suite with one worker. The suite reported **15 passed, 5 failed (18.4 minutes)**.
The wall-clock measurement was **1118.297 seconds** from queuing landing, of
which **1103.270 seconds** followed the build jobs. The process group was found
in the stopped (`T`) state near the end and resumed, so this duration includes
an unknown paused interval and is not a clean performance baseline.

The WebGPU and WebGL2 bridge traversal tests saw material **11 (soil)** where
they expected **24 (bridge deck)** in the cloned published tiles. Three WebGL2
tests timed out: the office spawn integration, facade/ID views, and four-tile
seam route. The WebGPU office integration tests passed. Details are in
`$TURKU3D_WORK/reports/host-slots-live-chrome.log` and the Playwright error
contexts. Other agents were still running unscheduled builds and Chrome suites
from older worktrees, so exclusive admission did not reserve capacity against
their processes.

Machine-wide load averages (1/5/15 minutes) were **107.71/150.66/139.49**
when the two synthetic builds had entered slots and **55.14/87.05/108.30**
after Chrome exited. An earlier baseline sample before this branch's work was
**60.63/125.82/152.00**. These snapshots include unrelated agent activity and
cannot be attributed to the scheduler alone.

Live-build Chrome acceptance needs a rerun after the bridge tile changes are
published and active worktrees use the scheduler. The scheduler's own gate and
synthetic admission checks are green; this real tile probe is not.
