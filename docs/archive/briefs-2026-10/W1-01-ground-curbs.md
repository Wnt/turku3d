# W1-01: Ground layer with real curbs (Phase 1 tasks 1.2–1.4)

Read `docs/briefs/COMMON.md` and `AGENTS.md`. Your layer is `ground` (COMMON §4).

## Goal

Replace the stub heightfield with the real ground of the office AOI. Curbs must be real steps in collision, so the
character steps up and a car bumps. The method is in ARCHITECTURE D5 and in the verified experiments in
`docs/research/reconstruction.md` (B3 variant: double breaklines snapped to the lidar edge).

## Inputs

- Lidar: `$TURKU3D_DATA/raw/laz-3x3/` (and `raw/laz`).
- Kantakartta WFS layers: 62520 curbs, 62522/3 sidewalk edges, katualueet surfaces with surface material.
- OSM for kerb semantics (dropped kerbs and crossings).

## Deliverables

1. **Ground points**: class 2 plus near-ground class 12 within 0.15 m of a robust local surface, denoised (1.2).
2. **Curb breaklines** (1.3):
   - Curb vectors become offset inner/outer lines snapped to the lidar edge.
   - The step comes from slope-fitted profiles every 1 m. Lines with a step above 1 m become `quay` (left for W1-02
     to model as walls); a step under 3 cm is flush (`curb_dropped` / `crossing`).
   - Check the steps against held-out stations, never the fitting set.
3. **CDT ground mesh** per collision tile (Triangle `pc`, adaptive base points), seams bitwise identical, with a
   per-triangle material from katualueet (road, sidewalk, curb, crossing, cobblestone, grass, ...) and an ortho
   fallback (1.4). Write the `ground` layer: `.col.npz` and the visual `.vis.glb` (with curb bevel geometry as
   visual-only).
4. **Water**: the Aura river surface becomes a sensor/visual plane at the water level. It must not be interpolated
   across, as the stub does.
5. **QA script and report**: the Phase 1 metrics 1.2–1.4 as JSON plus a short Markdown report with profile plots.

## Acceptance (from ARCHITECTURE §4)

- At least 95 % of 1 m open-ground cells have a point; noise MAD ≤ 1.5 cm.
- Held-out curb step error is under 3 cm at ≥ 90 % of stations with a step ≥ 6 cm. Control lines show a step under
  2 cm in ≥ 95 %. The collision curb transition is ≤ 0.20 m wide.
- Held-out lidar RMSE ≤ 3 cm on open ground; ≤ 150k triangles per collision tile; 0 open edges across seams; every
  triangle has a material.
- Unit tests on synthetic fixtures (a synthetic curb point cloud) run in a few seconds.
- A full AOI run is timed and cached per collision tile (re-runs are no-ops).
