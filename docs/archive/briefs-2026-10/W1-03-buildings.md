# W1-03: Buildings layer: shells, collision, facade-ready UVs (Phase 1 task 1.7)

Read `docs/briefs/COMMON.md` and `AGENTS.md`. Your layer is `buildings` (COMMON §4).

## Goal

Every building in the AOI plus margin is a closed shell. Nobody can walk or drive through one. Each shell has
facade geometry and UVs ready for photo-referenced textures (W1-09).

## Inputs

- City CityGML LoD2 (`bldg:Building_LOD2`, WFS) and city footprints.
- roofer LoD2.2 from lidar class 6. Roofer is installed by W0-C; see `docs/research/reconstruction.md`.
- OSM `building:levels`, `roof:shape` and entrances.

## Deliverables

1. **Model choice per building**:
   - Convert the city LoD2 to triangulated shells (earcut/cjio, not fan triangulation).
   - Run roofer LoD2.2.
   - Choose per building by the median and p75 absolute residual against class 6 points.
   - Snap footprints to the kantakartta and record the choice and the metrics.
2. **Collision shell**: closed and manifold, at most 2k triangles per building, material `building` (walls) and
   `roof`.
3. **Visual shell**:
   - Facades are split into planar facade polygons with a stable `facade_index` per building, ordered from the
     footprint start vertex.
   - UVs per COMMON §4 (u = metres along the facade, v = metres up). Write `extras: {building_id, facade_index,
     street_facing: bool, material_key}` so W1-09 can attach textures and the assembler can bind them.
   - Roofs get planar UVs and a roof `material_key` from the ortho colour class.
4. **Facade list for texturing**: `$TURKU3D_WORK/build/layers/buildings/facades.geojson`, one line per facade with
   base height, top height, normal, street_facing and `building_id`/`facade_index`. W1-09 consumes it; agree on the
   schema by writing it into `docs/specs/facades.md`.
5. Metrics JSON and Markdown report.

## Acceptance

- 100 % of AOI buildings have shells. The chosen model's median residual is ≤ 10 cm and p75 ≤ 25 cm. Shells are
  closed. Footprint Hausdorff distance to the kantakartta is ≤ 0.3 m.
- In the validator, 2,000 random walkers aimed at buildings end inside a footprint 0 times.
- Fast unit tests on a synthetic two-building fixture.
