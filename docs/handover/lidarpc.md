# lidarpc handover: LiDAR point-cloud overlay (2026-11-04)

The city's LiDAR survey streams into the game as COPC and draws over the world. The in-game editor turns it on;
`?pointcloud=1` shows it at startup. By default only vegetation and overhead wires draw (Jonni's direction).

Merged into `local/loop-integration`:

| Integration | Content |
| --- | --- |
| 09ce80ca | COPC data path: conversion tool, range server, client COPC/LAZ decode, LOD |
| b7a44031 | Rendering, opacity, editor hook, 2022 true-ortho colours |
| a0ee8171 | Direct-server fallback; laz-perf loaded as raw source (no Vite dependency pre-bundling) |
| d0da3e3f | Vegetation + wires by default, `setClassFilter`, derived wire layer |
| 72c00529 | Draws never exceed their instance buffers. A one-past count failed WebGPU validation, the pass stopped updating and the points froze on screen. |

## Data product (osgallery-dev, bulk HDD pool)

| What | Path |
| --- | --- |
| Main product | `/bulk/turku3d/lidarpc/copc/index.json` + `tiles/<E>_<N>.copc.laz` |
| Wire layer | `/bulk/turku3d/lidarpc/copc/wires/index.json` + `wires/tiles/<E>_<N>.copc.laz` |
| Raw LAZ cache (never in git) | `/bulk/turku3d/lidarpc/raw/pistepilvi/`, plus read-only `~/turku3d-bulk-warmup/data/raw/laz/pistepilvi/` |
| Tools | `/bulk/turku3d/lidarpc/tools/pdal/bin/` (PDAL 2.10.2, GDAL, untwine), `tools/venv` (laspy + lazrs, scipy, matplotlib) |
| Tool sources (deployed copies of the repo's `tools/pointcloud/`) | `/bulk/turku3d/lidarpc/src/tools/pointcloud/` |

### Tiles and frame
- Tiles are the city's 500 m `pistepilvi` tiles, 1282 city-wide (`data-manifests/laz-tiles.csv`). A tile's name is
  its NW corner (E min, N max).
- EPSG:3877 + N2000. Engine frame: `x = E - 23459500`, `y = H`, `z = -(N - 6704500)`.

### Main product (`turku3d-pointcloud@2`, recipe `copc@2`)
- LAS 1.4 PDRF 7 at 1 cm scale. The standard RGB is the survey's own colour, from a late-October flight (grey,
  leafless crowns).
- Extra bytes `OrthoRed/OrthoGreen/OrthoBlue` (uint8) hold the 2022 true ortho's colour, sampled from a 10 cm
  mosaic per tile. `0,0,0` means no ortho there.
- About 20M points and ~150 MB per tile.
- Index fields per tile: `name, file, bytes, points, min, max, cube, spacing, ortho_areas`.

### Wire layer (`turku3d-wires@1`)
- The survey has no wire class, so `tools/pointcloud/build_wires.py` derives one. Candidates are unclassified (1)
  and overlap (12) points that are:
  - more than 3 m above a 2 m ground grid;
  - in free air;
  - line-like and near-horizontal;
  - in segments at least 8 m across.
- Output is class 14 (wire conductor) with extra bytes `WireSegment` (uint32) and `HeightAboveGround` (float32).
- About 7k points and 80 KB per centre tile. The thresholds are in the index under `rules`.

### Class codes

| Code | Meaning |
| --- | --- |
| 1 | Unclassified |
| 2 | Ground |
| 3, 4, 5 | Low, medium and high vegetation |
| 6 | Building |
| 7 | Noise |
| 8 | Key point |
| 9 | Water |
| 12 | Overlap: the second flight line, unclassified, about 37 % of every tile |
| 14 | Wire (wire layer only) |

Tree crowns in overlap strips have about half their points in class 12.

## Server
- Systemd user unit **`lidar-copc`** (cityshare moved serving under it), `0.0.0.0:7960`, `Restart=always`.
- It runs `serve.py --root /bulk/turku3d/lidarpc/copc`: single-range 206 answers, CORS open, `index.json` no-store.
- Check: `ssh osgallery-dev 'systemctl --user status lidar-copc'`. Unit file: `~/.config/systemd/user/lidar-copc.service`.
- The game reads `./pointcloud/` (Vite proxy in `game/vite.config.ts`, target `TURKU3D_POINTCLOUD`). It falls back
  to `http://192.168.1.114:7960/` when the proxy answers non-JSON: a dev server started before the proxy existed
  answers with its SPA page. See `SOURCES` in `game/src/render/pointCloud/index.ts`.

## Client (`game/src/render/pointCloud/`)

### Modules

| File | Role |
| --- | --- |
| `copc.ts` | Header, extra bytes, hierarchy and record decode (pure) |
| `copc.worker.ts` | Range fetch + laz-perf |
| `lod.ts` | Projected-spacing selection under a hard point budget |
| `shuffle.ts` | Decode-time shuffle, so a prefix is a uniform subsample |
| `index.ts` | The overlay |

### Rendering
- One instanced draw per loaded node, of single-triangle discs, in the overlay's own pass.
- Points are depth-tested against the scene with a 0.3 m + 1 % bias and use the unjittered projection.
- They are mixed over the tone-mapped frame by the opacity (`PostComposite` in `render/post.ts`).
- Budgets in drawn points: low 0.6M, reduced 1M, medium 1.5M, high 3M. Max distance 1200 m; cache is 2.5x the budget.

### API
`window.__turku3dPointCloud` (type `PointCloudOverlayApi`); it also registers through `dev/editor/pointCloudHook.ts`:
- `setVisible/isVisible`, `setOpacity/getOpacity` (0..1), `setPointSize/getPointSize` (CSS px).
- `setColorMode/getColorMode`: `'rgb'` (ortho, else survey), `'survey'`, `'classification'`, `'elevation'`, `'intensity'`.
- `setClassFilter('vegetation' | 'all' | number[])` and `getClassFilter()`, plus `setClassVisible(cls, on)`.
  Meshes hold only the drawn classes, and the budget plans with each node's exact drawable count.
- `setOcclusion(bool)`, `setPointBudget(n)`.
- `queryNear(x, z, r)` returns `{count, density, minHeightM, maxHeightM, classes, depth, complete}`.
  `nearest(x, y, z, r)` returns the closest point. Both read loaded points of every class, whatever the filter.
- `snapshot()`.

### URL switch
`?pointcloud=1` (or a colour mode) shows the overlay at startup and pins it past edit mode. `?pointcloud=0` leaves it
out of the pipeline.

### Tests
`npx vitest run tests/unit/point-cloud.test.ts` (14 tests, synthetic PDAL fixture, no survey data).

## Background jobs (osgallery-dev; safe unattended: nice 19, ionice idle, 3.4 TB free on `/bulk`)

| Job | PID file | Log | State at handover |
| --- | --- | --- | --- |
| City COPC conversion (downloads raw LAZ, nearest-first, 4 jobs) | `/bulk/turku3d/lidarpc/build.pid` | `/bulk/turku3d/lidarpc/logs/build-city.log` | 144/1282 tiles after 2.3 h |
| Wire layer (2 jobs; rescans for newly downloaded raw tiles every 30 min) | `/bulk/turku3d/lidarpc/wires.pid` | `/bulk/turku3d/lidarpc/logs/build-wires.log` | following the downloads |

The city conversion takes about 1.3 tiles/min under farm load, so roughly 16 h in all.

- **Progress:**
  `ssh osgallery-dev 'grep -c " ok " /bulk/turku3d/lidarpc/logs/build-city.log; tail -2 /bulk/turku3d/lidarpc/logs/build-city.log; grep -c FAIL /bulk/turku3d/lidarpc/logs/build-*.log'`.
  The served index grows with it: `curl -s http://192.168.1.114:7960/index.json | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["tiles"]))'`.
- **Stop:** `kill $(cat build.pid)`, then `kill` its remaining `pdal pipeline` children by PID (never `pkill -f`).
  The wire job stops with `kill $(cat wires.pid)`.
  - The wire job's `--loop` keeps rescanning even after the city is done, since tiles that return 404 never appear.
    Stop it once the city log says `done`.
- **Resume** (finished tiles are skipped by source digest and recipe), from `/bulk/turku3d/lidarpc`:
  ```sh
  setsid -f nohup ./tools/venv/bin/python src/tools/pointcloud/build_copc.py --out /bulk/turku3d/lidarpc/copc \
    --raw /bulk/turku3d/lidarpc/raw --source /home/wnt/turku3d-bulk-warmup/data/raw/laz \
    --source /home/wnt/turku3d-drafts/data/raw/laz --ortho /home/wnt/turku3d-bulk-warmup/data/raw/ortho/ortho2022to \
    --pdal /bulk/turku3d/lidarpc/tools/pdal/bin/pdal -j 4 --threads 2 > logs/build-city.log 2>&1 < /dev/null
  setsid -f nohup ./tools/venv/bin/python src/tools/pointcloud/build_wires.py --out /bulk/turku3d/lidarpc/copc \
    --raw /bulk/turku3d/lidarpc/raw --source /home/wnt/turku3d-bulk-warmup/data/raw/laz \
    --source /home/wnt/turku3d-drafts/data/raw/laz --pdal /bulk/turku3d/lidarpc/tools/pdal/bin/pdal -j 2 --loop 1800 \
    > logs/build-wires.log 2>&1 < /dev/null
  ```
  Use `>>` to keep the old log. Deploy changed tools by copying the repo's `tools/pointcloud/*.py` to `src/tools/pointcloud/`.
- **Disk:** about 190 GB raw + 200 GB COPC at the end. The SSD is not used.

## Open items, ranked
1. **Street-level detail.** PDAL `writers.copc` nodes near the root are big (up to ~230k points), so the budget is
   spent in large steps.
   - At medium, the near field stops at 0.2–0.4 m spacing; the survey is 0.11 m.
   - Options: finer nodes (untwine, or a custom octree split), or a per-node near/far split at draw time. Raising
     the budget would also help: Jonni measured 39 fps / GPU p50 18.4 ms with the full cloud on.
   - No GPU timing of the overlay alone exists yet.
2. **Northern ortho gap.** `ortho2022to` covers only N ≤ 6715000 (centre and south), so the north shows the dull
   survey colour.
   - Fix: fetch the missing 2022 true-ortho areas (the city WMS layer "Ilmakuva 2022 True ortho", free) and rerun
     the conversion. Only tiles whose ortho changed rebuild, through the ortho digest in each sidecar.
3. **Editor filter binding.** The editor binds visibility, opacity, size and colour mode, not `setClassFilter`.
   - Suggested: hold Create + Y toggles `'vegetation'` / `'all'`, and add `'survey'` to its colour cycle.
     The note is in `~/turku3d/loops/editor/STATUS.md`.
   - Until then, use the console: `__turku3dPointCloud.setClassFilter('all')`.

Smaller items:
- **Tiles rebuilt in place:** file names carry no content key, so a page that already read a tile's hierarchy needs F5.
- **Overlap vegetation:** crowns in class 12 are not drawn by the vegetation filter, so overlap strips look thinner there.
- **Wires in crowns:** wires running through tree crowns are dropped by the free-air rule.
- **Loop notes and harness:** `~/turku3d/loops/lidarpc/STATUS.md`; headless frame harness in `~/turku3d/loops/lidarpc/harness/`
  (one SwiftShader Chrome on osgallery-dev; `run.sh`, `run-move.sh`).
