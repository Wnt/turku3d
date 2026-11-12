# W1-13: Lidar toolkit and lidar-first surface relief detection

Read `AGENTS.md` and `docs/briefs/COMMON.md` §3–4. Use the coordination board (`scripts/dev/codex-task board`,
`inbox`, `msg`).

## Why

The city lidar (2021, 63–86 pts/m², classified; `$TURKU3D_DATA/raw/laz-3x3/` holds the 9 tiles around the office) is
our richest source of real geometry, but every step so far reads LAZ ad hoc. The user wants the lidar **exploited
fully**, with **reusable tools** so it stays efficient as the city grows.

The first concrete use is the street surface. Curbs, kerb drops, pavement steps, ramps, verges, low walls and
planters must exist where the lidar shows them. That includes places the kantakartta vectors miss. Exact
centimetres are not the goal: presence in most of the right places is.

## Deliverables

1. **`turku3d.lidar` package**, fast and cached:
   - **Tile access**: a per-collision-tile cropped cache (COPC or npz with the dims we use; built once, memory-mapped)
     with a 50 m buffer option.
   - **Point queries**: by bbox, polygon, class or return; a KD-tree per tile, cached.
   - **Surfaces**: a robust ground surface (class 2 + near-ground class 12), DSM, nDSM and CHM rasters at a chosen
     resolution, with confidence/density layers.
   - **Profiles**: profile extraction along polylines (sampling, both sides, slope-fitted step estimation). This is a
     shared, tested implementation; W1-01's current one moves here if it is better.
   - **Edge and step detection**: height-discontinuity detection on the ground surface, linked into polylines with step
     height, upper and lower side, and confidence.
   - **Small-object extraction**: poles, bollards, benches, low walls and planters via nDSM blobs and shapes, as a
     typed list with heights (for later props).

   CLI + Python API, a README with examples, and fast unit tests on synthetic point clouds.
2. **Lidar-first relief detection for the AOI** (office block + 50 m margin):
   - Detect curb and step polylines from the lidar.
   - Match them to the kantakartta curb lines: confirm, refine the position, or **add the missing ones**.
   - Classify each one: curb, kerb drop, step, ramp edge, wall/planter edge, quay (step above 1 m).
   - Output a breakline file in the interface W1-01 announces on the board (polylines with step heights and sides).
     If W1-01 has not announced it yet, propose one on the board and agree on it.
3. **Coverage report**:
   - The fraction of kantakartta curb length confirmed by the lidar.
   - The added lidar-only relief length by class.
   - A map image over the ortho, plus the median and p90 step heights.
   - A short Markdown report.
4. **Integration**: with W1-01's layer, rebuild the ground for the AOI using the extra breaklines. Show before/after
   counts of curb length with a real mesh step. Only edit W1-01's code after a `request` on the board, or after its
   branch has landed.

## Acceptance

- `turku3d.lidar` is tested and documented. Cached tile access is at least 10× faster than reading the LAZ again
  (measure and report it).
- On the AOI: at least 85 % of kantakartta curb length is confirmed, and lidar-only relief is added where present.
  Before/after mesh-step coverage is reported.
- `scripts/dev/gate` is green, and acceptance is reported with numbers and the map.
