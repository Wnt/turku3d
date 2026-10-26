# Host slot scheduler verification (2026-10-26)

The scheduler adds host-wide `build` (3), `browser` (2), and exclusive priority
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

After rebasing on main at `f9d026a`, I cloned the published bridge-enabled tiles
into the sandbox and ran the full tile Playwright suite once through a `land`
slot. All ten WebGPU cases and both WebGL2 bridge cases passed; bridge traversal
reported material **24** on both backends. The suite finished with **16 passed,
4 WebGL2 timeouts in 21.0 minutes**. The scheduler wrapper measured **1263.175
seconds**. The WebGL2 facade/ID, ground texture, tile walk, and QA render cases
timed out. The log is `$TURKU3D_WORK/reports/host-slots-live-chrome-rebased.log`.

During the facade/ID case, I observed both the task process group and the
Playwright web server's separate process group in the stopped (`T`) state.
I resumed only those groups. The orchestrator's SIGSTOP mechanism was still
active despite the expectation that it would leave this task running. This
interruption and machine-wide load (124.34/128.95/102.98 while the suite ran;
45.14/80.82/87.79 shortly after) make the Chrome duration unusable as a clean
landing measurement. Landing priority reserves capacity against commands that
use the scheduler, so it covers the intended role of `quiet.sh` once active
worktrees run their heavy commands through slots. It cannot prevent an external
SIGSTOP or reserve capacity against commands that bypass the scheduler.
