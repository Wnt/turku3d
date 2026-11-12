# W0-C: Run the whole pipeline on the Mac, fetch the 3x3 working area, and build real tiles end to end

Read `docs/briefs/COMMON.md` first. You run in your own git worktree.

## Why

Everything moves to this Mac (M1 Max, 284 GB free). osgallery-dev is legacy. The pipeline currently syncs code to the
remote over ssh and runs in a remote conda env. Make it run **locally**, with the same reproducibility, so the Wave 1
agents can each build layers in their own sandbox. At the end, the rest of Wave 1 depends on `make tile` working
locally.

## Inputs already on the Mac

- `~/turku3d/data/` holds the remote `data/` mirror: `raw/{laz,wfs,ortho,osm}`, `dist/`, and `REPORT-office-block.json`.
- `~/turku3d/data/raw/laz-3x3/`: the 9 LAZ tiles around the office (`23459000..23460000` × `6704000..6705000`
  `<xmin>_<ymax>` names, 1.2 GB) with `SHA256SUMS`.
- `~/turku3d/research/`: the remote research mirror, including about 4 GB of Street View panos.
- `~/turku3d-data/` (old Mac mirror: `dist/`, `raw/streetview`, `textures/`, `reports/`). Consolidate it into
  `~/turku3d/data` and remove the old root once nothing references it.

## Deliverables

1. **Local env**: `pipeline/env/` gets a Mac (osx-arm64) environment. Use micromamba under `~/turku3d/tools/` and
   conda-forge for pdal, python-pdal, gdal, rasterio, geopandas, pyarrow, triangle, scipy, laspy+lazrs, shapely,
   pyproj, osmium-tool, pytest and pytest-xdist. Add:
   - KTX-Software (`toktx`)
   - gltfpack and gltf-transform (npm, pinned)
   - roofer (macOS arm64 build, see `docs/research/reconstruction.md`; if no binary exists, build it from source and
     document the steps)

   Provide `make pipeline-bootstrap` (idempotent) and `make pipeline-versions`. Remove the remote-only ssh/rsync paths:
   the `pipeline-*-remote` targets, `pipeline-sync`, `tools/dev/sync.sh` and `bootstrap-remote.sh`, so no dead remote
   code remains. Keep whatever tests are still meaningful and run them locally.
2. **Paths**: `pipeline/turku3d/config.py` defaults to `~/turku3d/data` on every OS. Raw data lives under
   `$TURKU3D_DATA/raw` (read-only for workers) and outputs under `$TURKU3D_WORK/{build,cache,dist,reports}`. W0-A adds
   `TURKU3D_WORK` and the sandbox plumbing; agree on the names in COMMON/W0-A and do not invent others.
3. **Working-area fetch**: extend the AOI config with a named area `work-3x3` (the 1.5 × 1.5 km covered by the 9
   LAZ tiles).
   - Fetch every kantakartta WFS layer the pipeline uses, the 2022 true ortho, the 2025 ortho, the drone layer where
     it exists, and an OSM clip, all with checksums in `data-manifests/checksums/`.
   - Register the 9 LAZ tiles already present instead of downloading them again.
   - The Phase 1 AOI (office block) stays the build target. The 3x3 area is for the margin, for fast expansion, and so
     that agents never wait on downloads.
4. **Tile build locally**: `make tile T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"` runs fully locally: fetch (cached), build,
   validate, e2e. It writes to `$TURKU3D_WORK/dist/tiles`.
   - Fix the open Phase 0 follow-ups in this chain: the cross-L0 seam steps in the stub tiles (up to 2.76 m), the
     `AOI_BORDER` flag set on interior seams, and the WMS fetcher caching blank chunks permanently.
   - Regenerate the QA reference crops (`make qa-refs`); there was a 0.64° heading fix.
5. **Speed**: profile `make tile` and remove avoidable work, for example re-reading whole LAZ files when a cropped
   COPC/LAZ cache per collision tile would do. Report the timings.

## Acceptance

- `make pipeline-bootstrap && make pipeline-versions` are green on a clean shell.
- `make tile T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"` is green locally, with seams bitwise identical across all 64 collision
  tiles (a test checks this).
- `make tile-show` serves the result, and the character walks across all four L0 tiles without steps at the seams.
- `~/turku3d-data` is consolidated and gone. No code references it, or osgallery-dev, except the docs' history notes.
- Fetch reports and checksums for `work-3x3` are committed.
