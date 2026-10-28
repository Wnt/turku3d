# Visual QA against real photos

The game renders a reference camera twice (colour and object IDs). A bounded pool of
Codex `gpt-6-astra` calls compares the photos, then writes asset-linked issues, scores,
quota observations and an HTML report. References are private QA inputs, not game assets.

## Run on the Mac

From the repository root, with the sandbox environment exported:

```sh
source ../sandbox.env
make qa-venv game-install
make -j2 qa-test game-test
make tile-qa DIST_MIRROR="$TURKU3D_WORK/dist" \
  QA_RUN=office-current JUDGE=1 QA_WORKERS=3
make qa-calibrate QA_RUN=office-current QA_VIEW=sv_01
make qa-regress QA_BASELINE=office-baseline QA_RUN=office-current QA_MARGIN=0.5
qa/.venv/bin/python qa/acceptance.py --run "$TURKU3D_WORK/qa/runs/office-current" \
  --calibration "$TURKU3D_WORK/qa/calibration/calibration.json"
```

The server uses your sandbox dist port (base +1). `JUDGE=1` explicitly enables
judging; `JUDGE=0` or absent only renders. `QA_WORKERS` defaults to 3, maximum 8.
`QA_DATA` defaults to `$TURKU3D_WORK/qa`, then `$TURKU3D_DATA/qa`, or `~/turku3d/data/qa` without overrides. Registry paths
beginning `data/qa/` resolve under that directory, keeping the registry portable.
The game server serves `$TURKU3D_WORK/dist/tiles` (default `~/turku3d/data/dist/tiles`
outside a sandbox). The native Mac `tile-fetch`/`tile-build` targets check shared raw
inputs and write local sandbox products; remote build/sync targets have been removed.
`TILE_QA_PORT` defaults to the sandbox port base + 1 and can be overridden explicitly.
The W1-04 assembler provides the actual AOI;
the existing Phase 0 ground-only package is useful diagnostic evidence but does not
pass W1-11 acceptance.

Reports: `$QA_DATA/runs/<run>/report.html`, `issues.jsonl`, `run.json`, `judge/*.json` and
`renders/*`. Cross-run records: `$QA_DATA/history.jsonl`, `$QA_DATA/issues.jsonl`.
Run IDs are immutable: choose a fresh ID, rather than overwriting old evidence. A run
records full git SHA, dirty state, exact reference image/pose hashes, prompt/schema hash,
model, browser channel, backend and package manifest hash/content. Partial rendering,
missing IDs, missing photos, judge errors and quota stops exit nonzero. A no-judge run
is never a regression baseline.

## The 20 reference views

`views/views.jsonl`: 10 Street View crops, 3 Look Around crops, 4 Mapillary perspective
images, 2 boat-level Google photospheres (2023), 1 drone panorama (2016).
The Street View crops cover multiple positions along the frontage; Look Around uses
three headings from one co-located camera. They are views, not 20 independently surveyed
camera stations. Source dates and explicit pose uncertainty are preserved in each row.

Reproduce the set from the existing read-only caches:

```sh
qa/.venv/bin/python qa/views/mapillary.py \
  --ids 773201984101318,611404776950262,1553002508431099,4652560791426523
qa/.venv/bin/python qa/views/prepare.py \
  --street-cache "$TURKU3D_DATA/raw/streetview" \
  --photo-cache /Users/wnt/Documents/game-test/data/refs \
  --mapillary-cache "$TURKU3D_WORK/qa/mapillary"
```

Street View/Look Around use the consolidated `$TURKU3D_DATA/raw/streetview` cache.
The separate `--photo-cache` path above still holds the Phase 0 tour/photosphere downloads
on this Mac; override it if those files move. `prepare.py` reads both caches without modifying them.
Only derived reference crops go into the sandbox. Mapillary uses the existing token at
`~/.config/turku3d/mapillary.token` in an Authorization header. It saves neither that
header nor temporary signed download URLs. Intrinsics follow the
[Mapillary API fields](https://mapillary.github.io/mapillary-python-sdk/docs/mapillary.config.api/mapillary.config.api.entities/):
normalized focal, k1 and k2; images are undistorted and cropped 4% at the borders. Their
pitch, roll and camera height are still approximate, so pose confidence is low.

For additional sources, existing tools remain available:

```sh
qa/.venv/bin/python qa/views/sv_views.py search --near 23459668,6704254 --half 60
qa/.venv/bin/python qa/views/sv_views.py make --pano hOH16IE5o9B6ar0x7Cm-9w \
  --look-at 23459668,6704254 --pitch 10 --fov 60 --id extra_front
qa/.venv/bin/python qa/views/tour360_adapter.py --root "$TOUR_CACHE" make \
  --pano B5D39CE1 --heading-offset -1.00208 --tour-yaw -12 --pitch -26 --hfov 60 --h 70.5
```

`make qa-refs` refetches Google Street View only; use `prepare.py` for the full mixed-source
set. `tools/refs/tdv360.py` and `tools/refs/gmaps_photospheres.py` acquire their raw source
caches (see `docs/research/reference-photos.md`). No paid APIs or new credentials are needed.

## Pose evidence

Coordinates and engine origin come from `pipeline/turku3d/config.py`. Pose fields are
EPSG:3877 E/N, N2000 camera-centre H; yaw is clockwise from grid north, pitch positive up,
roll positive clockwise as viewed from behind, and **fov is vertical**. Panoramic true
bearings are converted to grid bearings before cropping.

The drone heading is solved by circular least squares against cathedral and Michael
church spires. `drone-heading.json` includes geographic landmarks, source links and measured
panorama columns. The fit is approximately -1.002 degrees in grid coordinates, with a
0.039-degree two-landmark residual. This is **not absolute pose accuracy**: landmark
centres, manual image measurements and drone GPS have larger uncertainty. H=70.5 comes
from EXIF and is not a verified N2000 altitude. The view remains low confidence.

Look Around heading is counter-clockwise. Its stitched equirect centre faces backwards,
so true centre heading is `-heading + 180`, then converted to grid bearing. This was checked
against co-located Google panorama `de5igFTgjff1c9FgktKFXQ`; the historical `heading - 90`
guess was removed. The check uses persistent facade corners, not season or construction fences.

```sh
qa/.venv/bin/python qa/views/refine.py --ids sv_01,sv_02,la_02 \
  --laz "$TURKU3D_DATA/raw/laz/pistepilvi/23459500_6704500.laz"
```

This renders nearest visible lidar points in intensity, searches SIFT features (ratio
0.7), then attempts PnP RANSAC with separate held-out correspondences. Acceptance needs
at least 16 depth-backed matches, 12 inliers, held-out p75 <3 px and a bounded pose change.
It writes candidate poses/evidence; it never silently updates the registry. The first
three real views produced 0, 1 and 2 depth-backed matches, so **none was registered**.
Cross-modal intensity matching is not a substitute for validated alignment. Skyline
matching is not used. Cache/registration artifacts live in `$QA_DATA/registration`.

## Game render and asset IDs

```sh
node qa/render/render.mjs --views qa/views/views.jsonl \
  --url "http://localhost:$((TURKU3D_PORT_BASE + 1))/" --out "$TURKU3D_WORK/qa/renders" \
  --backend webgpu --channel chrome
```

URL contract: `?cam=E,N,H,yaw,pitch,fov&roll=R&qa=1&pass=color|id&backend=webgpu|webgl`;
optional `time` is ISO time with UTC offset. QA mode freezes dynamic actors and camera,
hides HUD, and settles loading/rendering. The viewport is exactly reference width × height.
The game exposes `__qaReady`, `__qaError`, `__qaPose`, `__qaInfo`, `__qaIdTable` and optionally
`__qaProject(E,N,H)`. Missing/nonfinite pose fields, including roll, fail the driver.
Failed captures remove stale PNGs. The loop additionally checks both pass metadata and
actual image dimensions before spending judge quota.

ID = `R<<16 | G<<8 | B`, zero means sky. The ID pass disables tone mapping, sRGB encode,
fog, shadows and antialiasing. It inherits layer `{layer, object_id, material_key}` and
package `{kind,id,tile}` extras, preserves `building_id`/`facade_index`, and separates
material groups. `objects[]` lists up to five overlapping assets with at least 2% bbox
coverage. This is a visible-overlap candidate list, not a semantic guarantee.
For `missing_object`, `objects[]` is empty and `mapping_status` explicitly requests a
spatial lookup: background ground is retained in `overlapping_objects[]`, never falsely
presented as the missing building's ID.

## Judge, calibration and regression

Every call/retry checks the highest reported Codex quota window and refuses unknown usage
or usage >= cap (default 100). The bounded scheduler stops new submissions after a quota
failure; already-running calls may finish. Results record before/after percentages, tokens,
thread IDs and seconds. These are account-wide integer counters, so concurrent agents and
rounding prevent attributing the percentage delta precisely to this task.

The prompt scores permanent massing, facades and ground features. Cars, people, boats,
weather, season, exposure and foliage density are ignored. Low-confidence poses suppress
unsupported displacement claims. `qa-calibrate` makes three independent calls on the exact
same real pair and reports mean, **sample variance**, standard deviation and range. A
three-call low-score result does not establish reliability across the full score range.

`qa-regress` requires explicit baseline and current IDs, complete scores for the same view
set, and identical photo/pose, prompt/schema, model, backend and ID-image context. It fails
when mean score delta is less than `-QA_MARGIN` (default 0.5); missing/incomparable evidence
also fails. Changed game/package commits are expected. The report's trend is restricted
to compatible comparison identities. Dirty source state is labelled, never disguised as
an immutable commit measurement.

## Offline tests

`make -j2 qa-test` runs synthetic fixtures only (no model/API calls or lidar files).
`make game-test game-build` checks the ID-pass integration. Browser tests use the sandbox
port base; run expensive real rendering separately to avoid GPU contention and distorted
test timing. `make qa-loop-stub` remains a plumbing test and is stored as kind `stub`,
excluded from real regression comparisons.

For a self-contained private review attachment (all reference/render images embedded):

```sh
qa/.venv/bin/python qa/export.py "$TURKU3D_WORK/qa/runs/office-current/report.html" \
  "$TURKU3D_WORK/qa/office-current.html"
```

## Sandbox execution

The stub defaults to `TURKU3D_PORT_BASE + 2`; `--port` can override it explicitly.
Browser profiles are created under `$TURKU3D_BROWSER_DIR/tmp`. `make qa-loop` defaults to the sandbox dev URL.

## Selective quality checks

From the repository root, run `scripts/dev/gate --base HEAD` for local changes or
`scripts/dev/gate --all` for the complete offline suite. See the [quality gate guide](../scripts/dev/README.md)
for dependency setup, path selection, caches, sandbox ports and Git hooks.

### Shared Codex accounting

The judge records every attempt, including retries and timeouts, in
`~/turku3d/ledger/codex-calls.jsonl` (`TURKU3D_CODEX_LEDGER` overrides it for tests).
Defaults come from `textures/codex-routing.toml` `[qa]`; `--model` and `--effort` override them.
Usage comes from completed-turn events; missing usage remains unknown. To inspect costs:

```sh
source ../sandbox.env
scripts/dev/codex-cost --by job_type
```

The fixed planted-difference model pilot and its quota limitations are documented in
[`bench/codex/README.md`](../bench/codex/README.md).
