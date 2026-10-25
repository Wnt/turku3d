# textures/ — Codex texture tooling ([M] Mac only)

Python package `turkutex` (Python 3.12, `textures/.venv`) + a small Node KTX2 encoder (`node/`).
Images never go into git: generated data lives under `$TURKU3D_WORK` (default `$TURKU3D_DATA` or
`~/turku3d/data`). Background: `docs/ARCHITECTURE.md` D6/D7/D8, `docs/research/ai-textures.md`,
`docs/research/streetview.md`, style rules in `style/STYLE.md`.

```
turkutex/codex/      runner.py (jobs.jsonl -> out.jsonl), rollout.py (thread_id -> images + prompts), usage.py (quota)
turkutex/streetview/ facades.py (footprints -> facade lines), panos.py (cache), camera.py (equirect/pinhole), refs.py
turkutex/post/       tileable.py, maps.py (DeepBump/Sobel, ORM), qa.py (seam ratio), pipeline.py (-> KTX2)
turkutex/jobs.py     label / improve job builders     prompts/ schemas/ style/   node/ (ktx2-encoder)
pilot/               facades.geojson (2 office-block facades) + make_previews.py     library/manifest.jsonl
tests/               pytest (fake codex binary: no network, no quota)
```

## Setup and tests

```bash
cd /Users/wnt/Documents/game-test
make textures-setup        # textures/.venv + npm ci in textures/node + DeepBump clone in <data>/tools/DeepBump
make textures-test         # 29 tests
make textures-usage        # {"used_percent": 1, ...}  (free: codex app-server JSON-RPC, no model call)
```
`streetlevel==0.12.13` is installed with `--no-deps` plus `requirements-streetlevel.txt` (its `pyfrpc` dep does
not build on macOS 26; `pyexiv2` is stubbed in `turkutex/streetview/sl.py`). No torch is needed.

## 1. Codex runner (`turkutex codex run`)

```bash
.venv/bin/python -m turkutex codex run JOBS.jsonl --out RUN_DIR [-j 4] [--max-pct 100] [--retries 1] [--no-cache] [--force]
.venv/bin/python -m turkutex codex collect <thread_id> [--copy DIR]     # images + exact image_gen prompts of one thread
```
- Job line: `{"id", "prompt" | "prompt_file", "vars", "images" (attached with -i), "files", "schema"
  (--output-schema), "model" (gpt-6-astra), "sandbox" (read-only), "timeout", "expect_images", "prompt_version", "meta"}`.
  Full spec in the `runner.py` docstring.
- Each call: `codex exec -m gpt-6-astra -C <fresh tmpdir with copies of the inputs> --skip-git-repo-check
  --sandbox read-only --json -o <last msg> -c notify=[] [--output-schema S] - -i img...`, prompt on stdin.
- **Quota**: weekly `usedPercent` is read before every call; ≥ `--max-pct` (default 100, `$CODEX_MAX_PCT`) or unknown →
  no new calls, exit 3. Rerun the same command to resume (ok jobs with the same content key are skipped).
- **Collection**: `thread_id` from `thread.started`; all PNGs in `~/.codex/generated_images/<thread_id>/` are copied to
  `RUN_DIR/images/<id>/NN.png` (several per thread happen); the one referenced by the image_gen output hint is `primary`.
- **Provenance**: the rollout `~/.codex/sessions/.../rollout-*-<thread_id>.jsonl` gives the exact image_gen prompt,
  referenced images, tokens and the float quota → `imagegen_calls`, `tokens`, `usage_after_rollout` in `out.jsonl`.
- **Cache**: `sha256(model, sandbox, prompt, schema, input hashes, prompt_version)` → `<data>/textures/codex-cache/`.
- Outputs: `out.jsonl` (last record per id wins), `failed.json` (failures + jobs not started because of the quota),
  `usage.jsonl` (before/after per call), `runner.log`, `logs/<id>.events.jsonl|.err|.last.txt`.
- The visual QA loop (D8) can use it as is: one job per view pair with its own schema and `expect_images: 0`.

## 2. Street View facade references (`turkutex facades`, `turkutex refs`)

```bash
.venv/bin/python -m turkutex facades --near 23459668 6704254 --radius 70 --out /tmp/facades.geojson [--containing]
.venv/bin/python -m turkutex refs pilot/facades.geojson [--only lr15_se] [--sources google,lookaround] [--max-views 4] [--out DIR]
```
- Input: GeoJSON LineStrings `[[E0,N0],[E1,N1]]` in EPSG:3877 with `id`, `height_m` (wall height), optional `base_h`
  (N2000) and `normal` (`right` = outward side for a CCW footprint). `facades` derives street-facing runs from
  kantakartta `kanta:Rakennus` 66000 footprints + `GIS:Keskilinjat` (WFS cached in `<data>/raw/wfs/facades/`); heights
  are `floors*3.2+0.8` until lidar eave heights exist.
- Panos: Google coverage z17 tiles around the facade, each spot resolved to its **newest time layer**; depth fetched
  with `find_panorama_by_id(..., download_depth=True)` (retry without depth) and **`np.fliplr`-ed on load**; camera
  Z = Google `elevation`; ground = elevation − campaign camera height (2026: 2.31, 2022/24: 2.86, 2019: 1.49 m).
  Apple Look Around: side faces pasted into an equirect (no torch); azimuth convention checked against a
  co-located Google pano (2.1° offset). Cache: `<data>/raw/streetview/{coverage,google,lookaround}/` (Mac).
- Selection: full-facade views plus "partial" detail views of long facades (section centred on the camera's
  foot point); score = angle × distance × (1 − depth-proxy occlusion) × resolution; ≥ 1 full view if any,
  2–4 views with angle/section diversity; views > 50 % occluded by nearer depth planes are rejected.
- Output per facade `<data>/textures/refs/<id>/`: `vK_*.jpg` (pinhole crop aimed at the facade centre, FOV fitted
  to the 4 corners), `_outline.jpg` (facade in red), `_mask.png` (255 facade, 128 depth-occluded, 0 other),
  `.json` (pose: E/N/H, yaw/pitch/FOV, pano heading, date, cam height, corners in px, scores), `refs.json`.

## 3. Post-processing (`turkutex post`)

```bash
.venv/bin/python -m turkutex post IN.png --out DIR [--tile xy|x|none] [--size 2048x512] [--material wood_painted] [--sobel] [--no-ktx2]
.venv/bin/python -m turkutex qa a.png b.png --axes x
```
Resize (power of two) → low-frequency flatten (`xy`, or `x` = per-row for facades) → make tileable: `xy` min-error
cut quilting with the new seam on the quietest column; `x` strips are cropped to a whole number of bays (autocorrelation
period, cut on quiet wall columns) and feathered → DeepBump normals + height (Sobel fallback) → ORM heuristic
(AO from height cavities, roughness preset + detail, glass) → KTX2 via `node/encode.mjs`: albedo ETC1S sRGB, ORM
ETC1S linear, normal UASTC + zstd + RDO, all with mips → `post.json` (QA before/after, map seams, KTX2 headers, sha256).
QA: seam ratio < 1.5 or seam rank < 95 (`turkutex/post/qa.py`).

## 4. Pilot (office block, Läntinen Rantakatu 15)

```bash
make textures-pilot            # refs -> label (strict schema) -> improve -> post -> previews; cached and resumable
make textures-pilot-previews   # docs/pilot/textures/*.jpg + pilot.json, library/manifest.jsonl
```
Facades (`pilot/facades.geojson`): `lr15_se` = Läntinen Rantakatu 15 river facade (38.8 m, 1 storey wood, → 6-bay
repeatable strip, `--tile x`) and `aurakatu2_se` = Aurakatu 2 (City Hall) facade on Läntinen Rantakatu (22.5 m,
2 storeys, → whole unique facade, `--tile none`). Results and Codex usage: `docs/pilot/textures/README.md`.

## Known limits
- Wall heights in the pilot are estimates; the reference tool takes `height_m`/`base_h` from the buildings worker later.
- Look Around elevation datum/camera height is not calibrated against lidar (2.4 m assumed for the ground estimate).
- Google depth is a planar proxy: trees/scaffolding are not detected as occluders (Codex labelling flags them).
- `~/.codex/AGENTS.md` makes free-text answers Finnish unless the prompt asks for English (prompts now do).


## Sandbox paths

Source `../sandbox.env` from a worker repository root before running the Make targets above.
`textures_data_dir()` and `tools_dir()` use `TURKU3D_WORK`; Street View and facade WFS downloads use
its `cache/streetview` and `cache/wfs/facades`. These runners never write shared `TURKU3D_DATA/raw`.
`TURKUTEX_STREETVIEW` remains an explicit cache override. The committed library manifest's `data_dir`
is relative to `TURKU3D_WORK` so it is portable between workers.
