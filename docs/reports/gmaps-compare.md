# Google aerial comparison investigation — 2026-10-28

Status: BLOCKED; tooling and synthetic tests are implemented, empirical acceptance is incomplete.
No Google image, crop, mesh, tile response or texture is included in this report or repository.

## Pose findings

The example permalink converts horizontally to E **23460939.121921**, N **6705112.305334**.
The brief's bridge E 23460983, N 6705006 is a landmark, not that camera location; substituting
it as the camera would shift the camera about **115.00 m**. The grid-bearing correction at
the permalink location is **+0.6175366°**: heading 139.27° becomes game yaw **139.8875366°**.
Tilt 69.94° corresponds to game pitch **−20.06°**. These are coordinate/convention calculations,
not landmark-measured image alignment errors.

The consumer URL's altitude datum remains unresolved. The code requires a declared height
hypothesis and offset instead of treating N2000 as ellipsoidal height. The roundtrip tests
exercise both the geoid addition and ground subtraction signs. They cannot validate which
one Google uses. Likewise, copying `35y` to vertical fov is an explicitly unverified hypothesis.
No altitude error, fov error or pixel reprojection error has been measured.

## Headless observation

Chrome exposed WebGL 2.0 on **ANGLE Metal Renderer: Apple M1 Max**. Nevertheless, the requested
Hämeensilta URL repeatedly normalized to `@60.4546407,22.2929172,265m`, a flat satellite view.
The observed center is 230.671 m from the input URL location; its implied height for the
requested tilt is 84.231 m. That is a useful diagnostic, not proof of the vertical datum.

Configurations tried: `--use-angle=metal`, `--ignore-gpu-blocklist`, `--enable-webgl`,
`--enable-gpu`, URL `force=webgl`, a normal Chrome user-agent, and disabling the automation
Blink feature/default argument. None retained the requested tilted scene on navigation.
The Layers → More → Globe view UI enabled a 3D control; navigating back to the requested URL
reset the preference and returned to flat satellite. Subsequent fresh-session Globe toggles
also failed to expose a clickable tilt control within 10 seconds. Local screenshots were inspected.
The task consumed its 5 browser-minute allocation; an extension was requested from the orchestrator.

The CLI detects this failure and exits nonzero before claiming a comparison. A controlled
Globe initialization / pose-preserving navigation solution is still required. No broad city
sweep was run to compensate for the failure.

## Acceptance and texture study

| Requested check | Measured result |
| --- | --- |
| Hämeensilta aligned pair | Blocked by 2D fallback; no alignment score |
| Teatterisilta, Myllysilta, Läntinen Rantakatu 15 | Not captured pending working 3D mode |
| Two Kupittaa streets | Not captured pending working 3D mode |
| Accepted landmark pairs | **0 / 6** |
| Truth-labelled walls assessed | **0 / 20** |
| Wall/roof colour errors and storey accuracy | **Not measured** |

The brief's data-root paths were absent. The truth owner subsequently supplied the actual
research root, `/Users/wnt/turku3d/research/facade-cv/`. Its `truth100/agreed.json` is empty;
`truth100/whole/agreed.json` contains six agreed labels, not 100. The corrected collection
is still being reconstructed. The current parameter file is
`textures/turkutex/facade_hybrid_parameters.json`.

The allowed fallback `labels/*/descriptions.json` contains **425** rows, of which **278** have
`source=reference`, a visible wall, wall colour and storeys. Counts by tile are 125, 21, 70,
62 for t0_0_0, t0_0_1, t0_1_0, t0_1_1 respectively. A deterministic selection of the first
five sorted eligible facade IDs per tile (**20 walls**) is saved locally at
`$TURKU3D_WORK/qa/gmaps-compare/study-selection.json`. No aerial observations are filled in.
The fallback labels do not supply roof-colour ground truth for these walls, so that metric
also needs an independent reference. Input selection is no longer blocked; aerial capture is.

After 3D capture works, record current parameters, judge aerial visibility (including
occlusion), and report paired colour error/storey accuracy with explicit usable denominators.
Missing observations must not be counted as zero error. No aerial-derived parameter, texture,
or geometry is shipped by this work.

The sanity-rule owner will leave the mandatory `make gmaps-compare` hook out until this tool
produces verified comparisons. Add that one-line hook after rebasing the completed tool slice.
