# qa/: visual QA loop (ARCHITECTURE D8)

Render the game from the pose of a real reference photo, have Codex **GPT-6-Astra** compare the two, and turn its findings into tickets (`issues.jsonl`) and an HTML report. The goal is "recognisably the same place", not a pixel match.

```
qa/views/views.jsonl ──► render/render.mjs (Playwright, URL contract) ──► <id>.png, <id>.id.png, <id>.id.json
        │                                                                         │
        └── reference image (data/qa/refs) ─────────► judge/judge.py (codex exec, qa.schema.json)
                                                                                  │
                                         report/report.py ◄── backmap (bbox → object ids)
                                         issues.jsonl, report.html, data/qa/history.jsonl (trend)
```

## Quick start (from the repo root)

```bash
make qa-venv            # qa/.venv (Python 3.12 + streetlevel, jsonschema, pytest) + qa/render/node_modules (playwright)
make qa-test            # 17 pytest + 4 node contract tests; no network, no Codex
make qa-refs            # re-create the reference images in data/qa/refs/ from the committed registries (downloads Street View)
make qa-loop-stub       # render all views with the contract stub, write a report, no Codex calls
make qa-loop QA_URL=http://localhost:5173/   # the real loop once the game implements the contract (Codex calls)
make qa-selftest        # planted-difference judge test (9 Codex calls, skips pairs already judged)
make qa-usage           # Codex weekly usage (no model call)
```

Data (images, renders, judge output) lives in `data/qa/` (git-ignored; override with `$QA_DATA`). Committed: code, `qa/views/views.jsonl`, `qa/selftest/other_views.jsonl`.

| Path | What |
|---|---|
| `qalib/camera.py` | **The camera convention** (normative, shared by Python, the stub and the game) |
| `qalib/pano.py` | equirect → perspective (numpy, bilinear, seam wrap) |
| `qalib/registry.py`, `views/view.schema.json` | registry load/validate/upsert |
| `views/sv_views.py` | Street View (and experimental Look Around) → posed reference views |
| `views/tour360_adapter.py` | turku.360tour.fi downloads (`tools/refs/tdv360.py` output) → posed views |
| `render/render.mjs`, `render/stub/index.html` | Playwright driver; reference implementation of the URL contract |
| `judge/judge.py`, `judge/prompt.md`, `judge/qa.schema.json`, `judge/codex_usage.py` | Codex judge and quota guard |
| `report/report.py`, `report/backmap.py` | tickets, HTML report, trend, bbox → object IDs |
| `loop.py` | one full pass: render → judge → report |
| `selftest/planted.py` | judge self-test with planted differences |

## 1. Reference view registry (`views.jsonl`)

One JSON object per line; schema `views/view.schema.json`. Example:

```json
{"id": "office_front", "image": "data/qa/refs/office_front.jpg", "width": 1280, "height": 960,
 "pose": {"E": 23459675.963, "N": 6704242.703, "H": 5.622, "yaw": 324.819, "pitch": 15.0, "roll": 0.0, "fov": 70.0},
 "time": null,
 "source": {"kind": "google_streetview", "pano_id": "hOH16IE5o9B6ar0x7Cm-9w", "pano_heading": 63.2, "url": "...", "note": "..."},
 "capture_date": "2019-06",
 "pose_confidence": {"level": "medium", "xy_m": 1.5, "h_m": 0.7, "angle_deg": 2.0, "note": "..."},
 "area": "office-block", "tags": ["office"], "created": "2026-10-25T16:20:00+00:00"}
```

Pose fields (degrees and metres):

| field | meaning |
|---|---|
| `E`, `N` | EPSG:3877 (ETRS-GK23) of the camera centre |
| `H` | N2000 height of the **camera centre** (not the ground) |
| `yaw` | compass bearing of the view axis, 0 = north, 90 = east, clockwise, `[0, 360)` |
| `pitch` | elevation of the view axis, + = up |
| `roll` | rotation about the view axis, + = camera turned clockwise as seen from behind (the scene appears rotated counter-clockwise) |
| `fov` | **vertical** field of view of the whole image height (= three.js `camera.fov`). `hfov = 2·atan(tan(fov/2)·W/H)` |

`time` is ISO 8601 local time **with offset** for the render sun, or `null` (Street View only gives the month). `source.kind` is one of `google_streetview`, `apple_lookaround`, `360tour`, `gmaps_photosphere`, `photo`, `synthetic`.

### Creating views

```bash
P=qa/.venv/bin/python
$P qa/views/sv_views.py search --near 23459668,6704254 --half 60          # panos, dates, bearing to the target
$P qa/views/sv_views.py make --pano hOH16IE5o9B6ar0x7Cm-9w --look-at 23459668,6704254 \
     --pitch 15 --fov 70 --size 1280x960 --id office_front --tag office      # yaw from --look-at (E,N[,H])
$P qa/views/sv_views.py make --near 23459668,6704254 --yaw 30 --fov 55 --id x  # nearest pano
$P qa/views/sv_views.py refetch                                             # rebuild missing images
$P qa/views/tour360_adapter.py list
$P qa/views/tour360_adapter.py make --pano B5D39CE1 --heading-offset 0 --tour-yaw -12 --pitch -25 --hfov 60 --size 1600x1200
```

- Street View pose: lat/lon → EPSG:3877 via `pipeline/turku3d/config.py`; `H` = Google `elevation` (looks like orthometric camera height; one-sample evidence in `docs/research/streetview.md`); the pano's own tilt (< 2°) is ignored. Confidence `medium` (about 1.5 m, 2°) until lidar registration (Phase 2) exists. Panos are cached in `data/qa/panos/`.
- Look Around (`--provider lookaround`) is **experimental**. It needs `uv pip install -p qa/.venv/bin/python torch torchvision pillow-heif`. The compass yaw of streetlevel's equirect centre is unverified: on one Turku pano, `heading − 90°` fitted by eye and the documented `heading + 180°` did not. It only searches the z17 coverage tile of the point. Poses are recorded as `low` confidence.
- 360tour: the adapter only reads `<root>/<pano>/meta.json` + `equirect_*.jpg` written by `tools/refs/tdv360.py` (the other worker's tool). It never downloads. Tour yaw is not north, so it needs `--heading-offset`, or a `heading_offset` key in meta.json. It also honours `camera_E/N/H` and `pose_confidence` keys if the downloader adds them later. Checked against `tdv360.py --view` output for the same view: mean abs difference 2.7/255.

## 2. Render URL contract (the game must implement this)

**Status (integration, 2026-10-25):** the game implements this contract (`game/src/main.ts` QA mode, `game/src/render/idPass.ts`). Against the pipeline stub tiles: `make tile-qa` (serves `game/dist` + `~/turku3d-data/dist/tiles`, runs `loop.py --no-judge`; `JUDGE=1` calls Codex), or with any server: `qa/.venv/bin/python qa/loop.py --url "http://localhost:8080/?tiles=/tiles/tileset.json" --no-judge` (`contractUrl` keeps the `tiles` query). All 3 registry views render in both passes with pose echo OK; `game/tests/e2e-tile/tile.spec.ts` checks pose echo, canvas size, `__qaProject` and the ID pixels.

`render/stub/index.html` implements it and is the reference; `render/test/stub.test.mjs` tests it.

**URL**: `<game>/?cam=E,N,H,yaw,pitch,fov[&roll=R][&time=ISO][&qa=1][&pass=id][&backend=webgpu|webgl]`

| param | required | meaning |
|---|---|---|
| `cam` | yes | six comma-separated decimals, exactly the registry pose (EPSG:3877 E,N; N2000 H of the camera; yaw/pitch deg; **vertical** fov deg). Example: `cam=23459675.963,6704242.703,5.622,324.819,15,70` |
| `roll` | no (default 0) | degrees, sign as in the registry |
| `time` | no | ISO 8601 with offset, URL-encoded (`+` → `%2B`), e.g. `2019-06-15T14:00:00%2B03:00`. Sets the sun and sky for that instant in Turku. Absent = the game's default QA lighting (fixed, deterministic) |
| `qa` | yes (driver sends `1`) | QA mode: no HUD/UI/cursor, no player avatar, no traffic, pedestrians or other dynamic actors, no camera smoothing or animation, no motion blur, temporal effects (TAA, auto-exposure) settled, fixed random seed, no pointer lock |
| `pass` | no | `color` (default) or `id` (below) |
| `backend` | no | force `webgl` (WebGL2) or `webgpu`; default = the game's normal choice |

Camera mapping in three.js (engine frame x = E−E0, y = H, z = −(N−N0), O = 23459500 / 6704500 / 0):

```js
camera.position.set(E - 23459500, H, -(N - 6704500));
camera.rotation.order = 'YXZ';
camera.rotation.set(THREE.MathUtils.degToRad(pitch), -THREE.MathUtils.degToRad(yaw), -THREE.MathUtils.degToRad(roll));
camera.fov = fov; camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix();
```

**Viewport**: the driver sets the viewport to the reference image's `width × height` with `deviceScaleFactor 1`. The canvas must fill the viewport exactly, at 1:1 pixels (no `devicePixelRatio` scaling, no letterboxing, no scrollbars).

**Page globals** (set on `window`):

| global | required | when / value |
|---|---|---|
| `__qaReady = true` | yes | set only when every visual tile (and texture mip) needed for this frustum has loaded and the frame is final: at least 2 frames rendered after the last load, shaders compiled, settled. The driver screenshots right after this. Timeout 120 s |
| `__qaError = "<msg>"` | yes on failure | bad `cam`/`time`, a pose outside loaded data, a device or WebGPU failure. The driver stops waiting and fails the view |
| `__qaPose` | yes | the parsed pose echoed back `{E,N,H,yaw,pitch,roll,fov}` (numbers). The driver checks it to 1e-3 |
| `__qaInfo` | recommended | `{renderer, backend, pass, time, three, tiles?: [...]}` stored in `<id>.meta.json` |
| `__qaIdTable` | yes in `pass=id` | `{ "<int id>": {kind, id, tile, material?, ...} }` (see below) |
| `__qaProject(E,N,H)` | recommended | returns `[px, py]` in CSS pixels (top-left origin) or `null` if behind the camera. Used to check that the game camera matches `qalib/camera.py` |

**Object-ID pass (`pass=id`)**: the same camera and frame with every visible surface drawn **unlit in a flat colour**. No antialiasing or MSAA, no tone mapping, no sRGB conversion of the ID colour, no fog, bloom, transparency blending or post effects. Object id = `R<<16 | G<<8 | B`; `0` (black) = sky/nothing. Each id must be in `__qaIdTable`, e.g. `{"1": {"kind": "building", "id": "<building id>", "tile": "t0_0_0", "material": "wall"}, "100": {"kind": "ground", "id": "ground_t0_0_0", "tile": "t0_0_0", "material": "sidewalk"}}`. `kind` is one of `building | ground | prop | vegetation | water | other`. For ground, an id per tile (optionally per material) is enough. `report/backmap.py` maps every issue bbox to the objects covering ≥ 2 % of it, which turns a ticket into "fix building X in tile Y".

**Driver**: `node qa/render/render.mjs --views qa/views/views.jsonl --url http://localhost:5173/ --out DIR [--ids a,b] [--pass color|id|both] [--backend webgl] [--channel chrome] [--timeout ms] [--extra "k=v"]`, or `--serve stub` (or `--serve <dir>`) to serve a static page itself. It writes `<id>.png`, `<id>.id.png`, `<id>.id.json`, `<id>.meta.json` (URL, timings, console errors, info) and `render-summary.json`. It exits 1 if a view failed, either by timeout, `__qaError`, a pose-echo mismatch, a wrong viewport or a missing id table. Chromium is launched with the WebGPU flags from `tools/engine-probe`.

## 3. Judge

```bash
qa/.venv/bin/python qa/judge/judge.py --ref data/qa/refs/office_front.jpg --render R.png [--id-image R.id.png] \
   [--context "reference from 2019-06"] [--cap 100] --out j.json        # --dry-run: print the command only
```

- Command: `codex exec -m gpt-6-astra --sandbox read-only --skip-git-repo-check -C <tmp> --output-schema qa/judge/qa.schema.json --json -o last.json - -i 1_reference.* -i 2_render.* [-i 3_object_ids.png]`. The prompt (`judge/prompt.md`) goes in on stdin. The `-` comes before the `-i` list because `-i` is greedy.
- Output schema `qa.schema.json` (strict: closed objects, all keys required):
  - `same_place`: yes | probably | unsure | no
  - `similarity`: 0–10
  - `summary`
  - `issues[]`: `{what, bbox:[x0,y0,x1,y1] normalised in the render, category: geometry|texture|material|lighting|missing_object|scale|vegetation|other, severity 1–5, fix_hint}`
  - `ignored_differences[]`
- The prompt asks for recognisability over pixel match. It ignores people, cars, boats, temporary site fences and scaffolding, weather, season, foliage, time of day, privacy blurs and small pose offsets unless `--context` asks for them. It gives score anchors (10 identical, 5–7 same place with clear errors, 0–1 different place), asks for tight boxes and English text (`~/.codex/AGENTS.md` asks for Finnish).
- Quota guard: before each call (and each retry) the weekly usage is read with `judge/codex_usage.py --percent`. This is a vendored copy of `osgallery-dev:~/leikkipuistodata/codex-usage.py` and makes no model call. If usage is ≥ `--cap` (default `$QA_CODEX_CAP` or 100) or cannot be read, the judge raises `QuotaError` and the CLI exits 3. Each result records `usage_before_pct`, `usage_after_pct`, the thread id, tokens and seconds. One retry if the answer fails schema validation.

## 4. Reports

`report/report.py data/qa/runs/<run>` (called by `loop.py` and `planted.py eval`) writes:
- `issues.jsonl`: one open ticket per issue (`run_id, pair_id, view_id, n, what, bbox, category, severity, fix_hint, objects[], status, ref, render, thread_id`), also merged into `data/qa/issues.jsonl`
- `data/qa/history.jsonl`: per-run scores, for the trend chart and the regression gate
- `report.html`: score table; per pair, reference and render side by side with numbered, category-coloured issue boxes (dashed = planted in self-tests), the issue table with back-mapped objects, the trend across runs of the same kind, and the thread id and usage per call

Re-running a run id replaces its rows, so it is idempotent.

## 5. Real test (2026-10-25): planted differences, Street View near the office

References: three Street View crops around Läntinen Rantakatu 15, all 2019-06 (`office_front` 14 m from the office coordinate, facing the facade; `office_oblique_sw` and `office_oblique_ne` along the street). "Renders":
1. the reference itself
2. a degraded copy: one facade recoloured (hue +180°, saturated), one building masked flat grey, the whole image Gaussian-blurred (r = 4 px)
3. Eerikinkatu 16 (Kauppahalli), about 230 m away, 2026-07

| pair | identical | degraded | other street |
|---|---|---|---|
| office_front | 10, yes, 0 issues | 5, yes, 3 issues (3/3 planted) | 0, no |
| office_oblique_sw | 10, yes, 0 issues | 6, yes, 4 issues (3/3 planted) | 0, no |
| office_oblique_ne | 10, yes, 0 issues | 6, yes, 3 issues (3/3 planted) | 0, no |

- Planted recall is **9/9**. The issue boxes land on the planted rectangles (e.g. planted grey `[0.62,0.08,1,0.72]` → reported `[0.619,0.079,1.0,0.726]`).
- The categories are sensible: grey block → texture/geometry, recolour → material, blur → texture with a whole-frame box.
- The one extra issue (sw) is real: the recolour turned a cloud orange, and Astra reported "an orange smear in the sky".
- Scores separate strictly: identical 10 > degraded 5–6 > other street 0.
- Each call takes 8–30 s and about 20 k input tokens.
- Codex weekly usage went from 0 % to 1 % over the 10 self-test calls (integer resolution) and was still 1 % after 3 more stub-loop calls.

A full loop over the stub (`run stub-judged`) also ran: scores 1–2, with "flat blocks, no windows" issues back-mapped to `office_LR15` and `lamp_post_1`, which exercises the ID pass end to end.

Limits:
- The planted differences are coarse. Subtle errors (wrong window count, a missing cornice, a slightly wrong roof pitch) have not been measured yet.
- Each pair was judged once, so run-to-run variance is unknown.
- Street View poses are not lidar-registered.
