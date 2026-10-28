# Local Google Maps / game comparison

**Experimental: headed 3D capture works; camera alignment remains unverified.** Six requested
locations produced local Google/game captures. Google changes the requested aerial pose when
3D is enabled, so the tool reads back its effective URL and renders the game from that pose.
The consumer URL's `a` altitude datum and `y` field-of-view interpretation have not been
established by landmarks. Supply a height hypothesis explicitly; every output retains the
`UNVERIFIED` label. Map labels/road vectors are not yet reliably removed.

## Commands

Source this sandbox's environment and install the existing project dependencies:

```sh
source ../sandbox.env
scripts/dev/wt sync-env
```

Run your requested game build on a sandbox port. For the currently published build, serve its
read-only files on this sandbox's dist port (this does not use Jonni's service):

```sh
node tools/dev/serve-dist.mjs --root "$TURKU3D_DATA/live/game" \
  --port "$((TURKU3D_PORT_BASE + 1))" --mount "/tiles=$TURKU3D_DATA/live/tiles"
```

A draft service can be started with `tools/dev/coarse-tiles` in this sandbox; see
[the service instructions](../dev/README.md#coarse-tile-service). Supply `--url` to compare a
particular sandbox game/dist/draft build; its query parameters are preserved. Cameras outside
the published tiles require those draft tiles to be ready before rendering.

Pure conversion with a **synthetic** 19 m geoid undulation (not a measured Turku calibration):

```sh
make gmaps-compare CAM=23459668,6704254,80,135,-45,35 \
  GMAPS_ARGS='--height-datum ellipsoid --height-offset 19 --pose-only'
```

Open visible sandbox Chrome with the unverified N2000 hypothesis:

```sh
make gmaps-compare GMAPS_ARGS='--google-url https://www.google.com/maps/@60.4562084,22.2901796,84a,35y,139.27h,69.94t/data=!3m1!1e3 --height-datum n2000 --height-offset 0 --label hameensilta'
```

The direct CLI also accepts `--at "23459668 6704254"` (camera location, default H=80 N2000,
yaw=135, pitch=-45, vertical fov=35), `--label`, `--url`, `--width`, `--height` and
`--settle-seconds`. `--cam` means camera location, not the point being looked at.
`--height-datum` and `--height-offset` are required:

- `ellipsoid`: offset is local quasigeoid height; `a = H_N2000 + offset`.
- `ground`: offset is ground H in N2000; `a = H_N2000 - offset`.
- `n2000`: offset must be zero; `a = H_N2000` (a calibration hypothesis only).

Use the official [NLS FIN2023N2000 model](https://www.maanmittauslaitos.fi/kartat-ja-paikkatieto/koordinaatit-ja-paikannus/koordinaattimuunnokset/fin2023n2000-geoidimalli)
for a real ellipsoid/N2000 offset. A horizontal EPSG:3877/WGS84 transform alone does not convert
heights. Google's [Maps API camera documentation](https://developers.google.com/maps/documentation/javascript/3d/camera-position)
describes an API, not a guarantee about the consumer Maps permalink.

## Capture and safety contract

Each invocation requests one view through the host `browser` slot in **visible headed Chrome**,
using only `TURKU3D_BROWSER_DIR/gmaps-compare`. This dedicated unsigned profile persists consent
between invocations; never sign into it. The first run opens google.com, chooses **Reject all**,
and checks for a persistent SOCS/CONSENT cookie before opening the full aerial URL. Cookie values
are never recorded. HTTP caching is disabled before navigation and service workers are blocked.
Chrome closes after the capture. There is no crawling, automatic navigation retry, Map Tiles API,
API key, tile-response reader, geometry export, or texture export. The tool requires this
sandbox's port range and rejects 5171/5175.

On this Mac, even a Google-produced aerial URL navigates back to flat satellite. The working
sequence is **Layers → More → Globe view**, then the accessible **Tilt the view** switch
(`role=switch`, not a button). The code enables these controls on the already loaded page,
waits for a tilted `a`/`t` URL, and reads it back without navigating again. The verified GPU is
ANGLE Metal Renderer: Apple M1 Max. Flags include `--use-angle=metal`,
`--ignore-gpu-blocklist`, `--enable-gpu`, and `--enable-webgl`; no headless flag combination
was demonstrated to preserve 3D on navigation.

The resulting pose may differ substantially from the request. `comparison.json` preserves
`requested_game_pose`, `requested_google_pose`, `google_final_url`, the effective `game_pose`
and `google_pose`, and `google_pose_delta` (heading wrapped to ±180°). `views.jsonl` describes
the effective game pose. This is a reproducible diagnostic, not a promise to hold the requested
camera fixed. The game capture uses `qa/render/render.mjs` readiness and pose-echo checks.

The screenshot retains Google's logo and attribution. CSS hides search/sidebar/category UI;
a reserved sidebar strip and some controls can remain. **This does not remove map-rendered
POI names or road vectors.** The requested clean-reference workflow additionally needs
**Layers → More → Labels off**; that setting has not been automated or verified in this slice.
The JSON explicitly records this limitation. Inspect the local image: a tilted URL and an
active tilt switch do not prove complete photogrammetry, overlay removal, or camera alignment.

Outputs are unique directories under `$TURKU3D_WORK/qa/gmaps-compare/`. A successful run writes
`gmaps-compare.png`, `google.png`, `game.png`, QA metadata and `comparison.json`. On flat fallback,
exit is nonzero and only a local `google-fallback.png` plus diagnostics is saved. The output path
cannot be in this repository. Never copy these files to a committed report, Pages, or a texture
input. The policy stage rejects comparison imagery paths even outside its normal image roots
and even if someone adds an allowlist entry. Delete local run directories when no longer needed.

## Verification

```sh
.venv/bin/python -m unittest tools.compare.test_pose tools.compare.test_cli
.venv/bin/python scripts/dev/tests/run.py test_policy.py
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
```

The fast gate runs only synthetic math/safety tests and lint; it never opens Google.
See [the investigation record](../../docs/reports/gmaps-compare.md) for measured corrections,
the headed workaround and outstanding acceptance work.
