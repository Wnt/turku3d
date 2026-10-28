# Google aerial comparison investigation — 2026-10-28

Status: tooling slice complete; empirical pose acceptance and the texture study are incomplete.
Headed Chrome produced six local 3D/game captures, all labelled **captured-unverified**.
No Google image, crop, mesh, tile response or texture is included in this report or repository.

## Working browser path

A visible, normal-size Playwright Chrome window used a dedicated sandbox profile throughout
the attempts. Opening google.com first and choosing **Reject all** set a persistent SOCS cookie.
No account was used. Both headless and headed navigation still normalized full aerial URLs to
flat satellite, including a URL Google itself had just produced. Consent persistence alone
therefore did not solve the failure.

The successful same-page sequence was **Layers → More → Globe view → Tilt the view**.
The last control has accessibility role **switch**, not button. Reading the new URL without
renavigating preserves the tilted scene. Six repeated requested views worked with this path.
Chrome reported WebGL2 on **ANGLE Metal Renderer: Apple M1 Max**. The capture tool now uses
headed Chrome with `--use-angle=metal`, `--ignore-gpu-blocklist`, `--enable-gpu`, and
`--enable-webgl`; headless photorealistic capture is not an accepted result.

The CLI saves both requested and effective poses, then uses the effective URL for the game
camera. Google can omit a zero heading (`0h`); the parser accepts that native form. The game
must pass the shared QA readiness and camera-echo checks before a pair is written.

CSS UI hiding was checked on the final Joukahaisenkatu view, retaining logo and attribution.
The six earlier captures still contain UI and map labels. A 72-pixel reserved sidebar strip
and some controls remain even after CSS hiding. The subsequent request for **Labels off**
(POI names and road/lane vectors absent) arrived after the browser session closed. The relevant
manual control is Layers → More → Labels; automation and overlay-free captures remain work.
No decoded Google mesh is used as a fallback by this tool.

The headed session ran from 07:13:39 to 07:44:42 UTC. Closing its interactive input loop hung;
only that owned session was interrupted. Its 1,863 seconds were manually debited because the
scheduler's interrupted command did not record them. Including the earlier roughly five-minute
headless investigation, usage was **36.05 / 35 browser minutes**. No further browser was opened.
The owned browser and servers on sandbox ports 5181/5182 were stopped.

## Pose findings

The example permalink's horizontal coordinates transform to E **23460939.121921**,
N **6705112.305334**. The brief's bridge E 23460983, N 6705006 is **115.00 m** away;
substituting it for the URL location would change the pose. Grid-bearing correction there is
**+0.6175366°**: heading 139.27° becomes game yaw **139.8875366°**. Tilt 69.94° maps to pitch
**−20.06°** under the stated convention. These are coordinate calculations, not measured
image alignment errors or proof of the consumer URL's camera semantics.

For the six-site experiment, seeds were 80 m south of each target at H=80 N2000, yaw=0,
pitch=−45°, fov=35°. Enabling 3D changed the horizontal URL location about **53.6–54.0 m
farther south**, `a` from 80 to **161–162**, tilt from 45° to **39.51–39.52°**, and heading
by **+0.608–0.646°** (wrapped). These are observed URL normalization deltas, not fitted pose
errors. The tool now preserves them instead of silently using the original game pose.

The consumer URL's altitude datum remains unresolved. The code requires an explicit hypothesis:
ellipsoid (add local geoid undulation), height above ground (subtract ground N2000), or N2000
(zero offset). Synthetic roundtrip tests verify the signs, not which hypothesis Google uses.
The official FIN2023N2000 grid sampled at lat 60.4478739, lon 22.2930656 gives **19.41436 m**
undulation. A diagnostic game render with a +19.414 m height shift did not establish a valid fit;
that offset is not adopted as Google's conversion. Copying `35y` to vertical fov is also unverified.
The Google viewport inset is another unresolved source of framing differences.

Visible corresponding buildings and roads demonstrate the same neighbourhood, but their screen
positions do not match sufficiently to accept alignment. No fitted altitude/FOV correction or
pixel reprojection score is claimed.

## Six-site evidence

All files stay under `$TURKU3D_WORK/qa/gmaps-compare/six-views/<label>/`, containing
`comparison.json`, `google.png`, `game.png`, and `gmaps-compare.png`.

| Label | Target E, N | Google 3D / game QA ready | Pose accepted |
| --- | --- | --- | --- |
| Hameensilta | 23460983, 6705006 | Yes / yes | No |
| Teatterisilta | 23459503.675, 6704105.544 | Yes / yes | No |
| Myllysilta | 23459146.428, 6703908.499 | Yes / yes | No |
| Lantinen-Rantakatu-15 | 23459668, 6704254 | Yes / yes | No |
| Lemminkäisenkatu | 23461522.066, 6703925.729 | Yes / yes | No |
| Joukahaisenkatu | 23461262.775, 6704354.596 | Yes / yes | No |

The game used independent cached draft tiles with published core tiles substituted where
available, served only in this sandbox. These were not rebuilt after the bridge generator fix
landed during the session; the images are not evidence of that fix's current output. Office
and Teatterisilta captures were rerendered after repairing the local published-ground mount;
the final records report QA ready. Accepted aligned pairs: **0 / 6**.

## Texture study

Truth-labelled walls assessed from aerial imagery: **0 / 20**. Wall/roof colour errors,
storey accuracy, and improvement over current parameters are **not measured**.

The brief's data-root paths were absent. The truth owner supplied the actual research root,
`/Users/wnt/turku3d/research/facade-cv/`. At inspection, `truth100/agreed.json` was empty and
`truth100/whole/agreed.json` held six agreed labels. The collection was still being rebuilt.
The current parameter file is `textures/turkutex/facade_hybrid_parameters.json`.

The allowed fallback `labels/*/descriptions.json` contained **425** rows, of which **278** had
`source=reference`, a visible wall, wall colour and storeys. Eligible counts were 125, 21, 70,
62 for t0_0_0, t0_0_1, t0_1_0, t0_1_1 respectively. A deterministic first-five-sorted-per-tile
selection (**20 walls**) is local at `$TURKU3D_WORK/qa/gmaps-compare/study-selection.json`.
No aerial observations are filled in. These labels lack roof-colour ground truth.

Remaining work is calibrated, overlay-free reference capture and paired scoring with explicit
visibility/usable denominators. Missing observations must not count as zero error. Any later
use of derived parameters remains Jonni's decision; this slice ships no aerial-derived assets.

## Verification and workflow

See [the CLI README](../../tools/compare/README.md) for exact capture and unit-test commands.
The fast gate only tests pure conversion and safety policy; it never opens Google. The sanity
rule now mentions `make gmaps-compare` at affected spots, local screenshots only, with an explicit
calibration-status caveat. It does not turn these unverified pairs into geometric acceptance.
