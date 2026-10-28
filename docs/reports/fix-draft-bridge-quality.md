# Draft bridge appearance and profile audit

Implementation, real-data builds and paired game-renderer inspection are complete. The private
Auransilta capture passed its highest-detail gate; its first bridge score was withheld after the
reference owner found insufficient local support for the one scored patch.

OSM crossing outlines and membership remain authoritative for one deck per crossing. Optional
classified city lidar supplies robust longitudinal deck profiles and positive support evidence.
A held-out fifth of the returns does not participate in profile fitting. Unsupported profiles
retain the ground-key-point fallback; no missing measurement is reported as a zero error.

The producer adds separate deck surfaces and structural bodies, edge beams, bank abutments,
wide supports/caps, open steel rails or explicitly tagged solid parapets, mapped raised walks,
lane paint, railway tracks/sleepers and mapped lamp posts. Teatterisilta's OSM four-head angled
lamps are retained. Material albedos are procedural; asphalt binds the shared street library.
No private reference imagery, geometry, colours or measured reference values enter generation.
Underside dimensions, untagged railing styles and unsupported pier layouts remain type estimates.

CPU geometry inspection of Teatterisilta exposed a sloping observation bay. Ground blending now
applies only to mapped branch paths. The same inspection verified two lidar-supported piers in
both adjacent tiles. These plots are geometry diagnostics, not game-renderer screenshots.

Collision now clips detailed bridge meshes to each 62.5 m cell instead of repeating whole
visual-tile meshes. Boundary vertices remain shared. Submillimetre sidewalk intersections are
simplified consistently for top, soffit and sides before export. Collapsed float32 boundary ears
are removed using the COL validator's area calculation. The incidental building-shell repair
welds micrometre ring edges before all shell faces use the same ring.

## Real-data evidence

Local output root: `$TURKU3D_WORK/reports/bridge-quality/{before,after}`. Both complete builds
succeeded for all 13 tiles: 27 clipped instances representing 24 distinct OSM crossing identities.
The baseline replaces only the bridge producer with `d78eb3c7bc45`; other layers use the audited
checkout. Supplementary WFS layers are in the sandbox cache, with shared inputs linked read-only.
The paired GLBs predate the independent draft-texture landing. After rebasing that producer,
the combined bridge, tunnel and surface tests pass (35 tests); its changed ground/material
appearance will be rebuilt by the integrator. No post-texture screenshot claim is made here.

Height residuals use the same held-out city-lidar returns in both builds. They include outliers
from furniture/other surfaces; robust medians are not maximum-error guarantees.

| Bridge | Before median (m) | After median (m) | After p90 (m) | Outline width minus lidar occupancy (m) |
| --- | ---: | ---: | ---: | ---: |
| Myllysilta | 1.041 | 0.021 | 0.049 | -1.247 |
| Martinsilta | 0.756 | 0.018 | 0.050 | -0.841 |
| Teatterisilta | 1.036 | 0.018 | 0.044 | -1.007 |
| Auransilta | 2.090 | 0.021 | 0.275 | +0.135 |
| Kirjastosilta | 0.292 | 0.021 | 0.040 | -0.659 |
| Tuomiokirkkosilta | 0.332 | 0.033 | 0.130 | -1.529 |

Widths retain OSM outlines; the occupancy comparison does not refit them. Hämeensilta and
Hämeenramppi lack the required full lidar block. Betanianpolku lacks accepted endpoint support.
The northern railway and Raunistula crossings have no usable full survey; their OSM dates also
postdate the available 2021 flight. Their height and width errors remain **unmeasured**.

The maximum after collision file is 429,512 bytes (`t0_-2_2/c_-6_9`). The railway cell that
previously exceeded the cap is now 395,088 bytes (`t0_3_-5/c_12_-19`), below the 1,000,000-byte limit.
Detailed bridge triangles, summed across clipped visual tiles: Myllysilta 23,362; Martinsilta
25,732; Teatterisilta 17,958; Auransilta 10,356; Kirjastosilta 13,186; Tuomiokirkkosilta 14,172;
Hämeensilta 36,206; Hämeenramppi 6,213; Raunistulansilta 29,546; railway 50,738; Betanianpolku 6,688.
These are geometry counts, not scene draw-call measurements.

## Game-renderer inspection

The sandbox game renderer captured 81 views for each build, using identical camera poses: top and
oblique views for every distinct crossing in the 13 affected tiles, plus street, bank and deck
views for each named bridge. The 48 after top/oblique images were inspected together, with full
resolution checks at the named river and rail crossings. The bridge shapes occupy the mapped
crossings once, and the widened Teatterisilta bay stays level. Auransilta's before/after bank
pair shows the former blank slab and sticks replaced by an edge beam, open rail, substantial
support and bank abutment. Hämeensilta's deck pair shows asphalt, lane paint, raised pavement
and rails in place of the bare slab. The river ground/water boundary remains visibly jagged in
these draft scenes; it is a separate source-ground issue.

The whole visible scene stayed at 53–161 draw calls after (50–156 before), below the 500-draw
bridge-spot budget. It drew 212,715–782,533 triangles after (141,686–590,040 before). These
counts include all loaded visible models, not only bridge meshes. For the three street/bank/deck
views of each named crossing, the after maxima were:

| Bridge | Max draws | Max scene triangles |
| --- | ---: | ---: |
| Myllysilta | 115 | 648,167 |
| Martinsilta | 150 | 782,533 |
| Teatterisilta | 135 | 677,476 |
| Auransilta | 154 | 695,628 |
| Kirjastosilta | 161 | 731,844 |
| Tuomiokirkkosilta | 88 | 340,790 |
| Betanianpolku | 149 | 749,139 |
| Hämeensilta | 90 | 581,968 |
| Hämeenramppi | 67 | 405,682 |
| Raunistulansilta | 96 | 657,218 |
| Aurajoen ratasilta | 103 | 664,954 |

The exact screenshots, performance JSON and own-source EPSG:3877/N2000 reference camera lists
are retained only under `$TURKU3D_WORK/reports/bridge-quality/`. A street camera at the raised
Hämeenramppi had no ground directly underneath; the QA harness now uses the bridge surface or
nearest mapped ground as support and records the adjusted pose for both builds.

## Commands

```sh
source ../sandbox.env
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_bridges.py pipeline/tests/test_coarse_tunnels.py
python3 -m unittest scripts.dev.tests.test_host_slots
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python -m turku3d.coarse.bridge_quality after --wfs-cache "$TURKU3D_WORK/cache/bridge-wfs"
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python -m turku3d.coarse.bridge_quality before --wfs-cache "$TURKU3D_WORK/cache/bridge-wfs"
npm --prefix game run dev -- --host 127.0.0.1 --port "$TURKU3D_PORT_BASE" --strictPort
# In another sandbox-sourced shell, render after first to freeze supported camera poses.
python3 scripts/dev/host_slots.py browser -- bash -c 'node qa/render/bridge-quality.mjs after && node qa/render/bridge-quality.mjs before'
```

The local supplementary fetch script is `$TURKU3D_WORK/cache/bridge-wfs-fetch.py`. It fetched
15 missing WFS layers into `cache/bridge-wfs/bridge-audit-*`; shared raw files were never modified.
The renderer samples street/bank/deck eye positions 1.7 m above the supporting after mesh and
reuses the exact positions for before. It records top/oblique views of every crossing and
street/bank/deck views of all named crossings, plus per-view performance counters.

The combined bridge/tunnel/surface tests passed after the texture rebase (35 tests, 1.66 s);
host-slot tests passed (14 tests, 8.295 s). The fast gate passed in 1.295 s after the
clone-safe Chrome launcher update.

## Admission fix and private reference status

The audit exposed a host scheduler error: a running code integration and its queued replacement
were counted simultaneously, preventing later builds even when CPU capacity was free. The
replacement-reservation check now removes only the current code reservation when checking the
future state. Current CPU/GPU limits and integration priority remain enforced. The later main
admission change allows three agent Chrome suites beside integration. A follow-up correction
compares each resource's queued-integration deficit before and after an admission: an existing
CPU shortfall from data/build work no longer blocks a browser that consumes only free GPU capacity.
Build work that worsens that shortfall still waits.

The private comparison tool rendered 24 offline reference views at our own-source camera poses:
street, bank and deck for Myllysilta, Martinsilta, Teatterisilta, Auransilta, Kirjastosilta,
Betanianpolku, Hämeensilta and Hämeenramppi. The matching before/after game views remain in the
sandbox; all reference images remain in the private store. The Auransilta/Kirjastosilta tile
passed 1,980/1,980 terminal-node completeness and tile calibration. Several low views still
contain provider holes or occlusion, so visible detail cannot be assumed everywhere. The other
four captured tiles are incomplete and their offline views are qualitative only. There is no
capture for Tuomiokirkkosilta or the two northern railway crossings yet.

An initial strict diff of the immutable Auransilta tile pair finished, but the private reference
owner identified a bridge metric eligibility bug: its lone bridge patch lacked the independent
masked support required for a confirmed height claim. **No bridge reference height score is
accepted or reported.** The owner is fixing eligibility and rerunning the pair. This does not
alter the independent held-out city-lidar residuals above, and no private reference values enter
the bridge producer. Reference images and detailed reports remain private.
