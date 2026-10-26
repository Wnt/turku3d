# Browser gate timing and isolation

Measured in the `tool-fast-browser-checks` sandbox on 2026-10-26, Chrome 156 on the M1 Max. Browser profiles, reports, ports and tile copies stayed in this sandbox. The real-tile runs used the 17-tile published release `435b3d9` copied into `$TURKU3D_WORK/dist/tiles`.

The brief reported 108–175 s for game e2e and 160–215 s for live Chrome. In the table, “Before” means the first local run; the first real-tile run already included the initial wait changes, so it is not an unmodified-HEAD baseline.

| Stage | Before | Current | Result |
|---|---:|---:|---|
| Synthetic game e2e, 46 tests | 81.0 s, 46/46 | 95.8 s at 960×540, 44/46 before restoring the motion pixel test's 1280×720 viewport | Speed target not met; the corrected full gate passes |
| Full gate browser stage, synthetic game and tile fixtures | 131.7 s, passed on the first branch slice | 156.3 s, passed after viewport and route fixes | Includes both suites; host load varied |
| Published real-tile Chrome, 20 tests | 226.4 s, 15/20 with fixed-wait and stale ground-pixel failures | 206.1 s, 20/20 after selecting an unobstructed published van | Speed target not met |
| Warm service, affected coarse-tile check | No persistent browser | 4.7 s WebGPU; 15.8 s WebGL2, both passed | Selected checks, not a full-suite substitute |

The real-tile run at 206.1 s had host load roughly 80–130. Its slowest tests were WebGL2 office integration (91.7 s), facade QA (87.7 s), and bridge traversal (57.6 s). The parked-van route previously timed out after reversing from the first metre; the x≈69 published van completed the unchanged >20 m driving assertion on both backends in a focused 25.4 s run.

An old/new release comparison at the same 2560×1440 office facade pose did not reproduce a serial page-load regression. Color QA reached ready in 12/8 s on WebGPU and 20/20 s on WebGL2 for `2cde573`/`435b3d9`; ID QA reached ready in about 2 s on both. The new release loaded 17 visual models versus 9 and made fewer office color draw calls (261 versus 281). This points toward concurrent Chrome/GPU contention in the full suite, but does not establish a single cause. The reported 52/93 s pose needs its exact URL and viewport to compare directly.

The suite now masks host gamepads in every ordinary and manually created test page, uses readiness signals in place of fixed pauses, keeps a real-tile HTTP cache per worker, and defaults route checks to 960×540. Pixel-detail, camera-projection and 1440p performance checks retain their explicit larger viewports. `scripts/dev/affected_browser.py` selects changed-path browser specs using the gate map. `scripts/dev/warm_browser.mjs` serves persistent Chrome endpoints on sandbox ports +3/+4 and runs selected checks with JSON timings.

The requested <60 s full game and live stages and 5/5 stress runs with two other builds have not been demonstrated. The host-slot scheduler was not on main during these runs.
