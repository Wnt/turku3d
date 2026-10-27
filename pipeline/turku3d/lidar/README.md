# Lidar toolkit

All coordinates use `turku3d.config`: points are engine **x/y/z**, polygons and
profiles are engine **x/z**, heights are N2000. Raw LAZ is read-only. Outputs live
under the caller's `$TURKU3D_WORK`. No service, key or network request is needed.

```sh
source ../sandbox.env
bash pipeline/env/run.sh python -m turku3d.lidar cache --tile c_2_3 --buffer 50
bash pipeline/env/run.sh python -m turku3d.lidar query --tile c_2_3 --classes 2 12 \
  --returns 1 --output "$TURKU3D_WORK/build/points.npy"
bash pipeline/env/run.sh python -m turku3d.lidar surfaces --tile c_2_3 --buffer 50 \
  --resolution 0.25 --output "$TURKU3D_WORK/build/surfaces.npz"
bash pipeline/env/run.sh python -m turku3d.lidar edges --tile c_2_3 --buffer 50 \
  --output "$TURKU3D_WORK/build/edges.json"
bash pipeline/env/run.sh python -m turku3d.lidar objects --tile c_2_3 \
  --output "$TURKU3D_WORK/build/objects.json"
# --line contains a JSON array of engine x/z vertices.
bash pipeline/env/run.sh python -m turku3d.lidar profiles --tile c_2_3 \
  --line "$TURKU3D_WORK/build/profile-line.json" --output "$TURKU3D_WORK/build/profiles.json"
bash pipeline/env/run.sh python -m turku3d.lidar benchmark --tile c_2_3 \
  --output "$TURKU3D_WORK/reports/lidar/cache-benchmark.json"
bash pipeline/env/run.sh python -m turku3d.lidar relief --scope office-block
bash pipeline/env/run.sh python -m turku3d.lidar relief --scope phase1
.venv/bin/python -m pytest pipeline/tests/test_lidar.py -q
scripts/dev/gate
```

`office-block` uses `config.OFFICE_BLOCK.with_margin`; `phase1` uses the full four
visual tiles plus the canonical 50 m margin. Coverage outputs are
`reports/lidar/<scope>/{coverage.md,coverage.json,coverage-map.jpg,breaklines.json,
added-breaklines.json,objects.json,surfaces.npz}`. The map is north-up over the cached 2022 true ortho.
Use `added-breaklines.json` when adding missing relief to existing city-vector ground;
loading all segmented mapped vectors as additions would duplicate its constraints.

```python
from shapely.geometry import LineString, box
from turku3d import config as C
from turku3d.lidar import TileStore, Surface, select_ground, profiles, surfaces

store = TileStore()  # defaults raw/laz-3x3 -> work/cache/lidar
points = store.tile("c_2_3", buffer=50)
subset = points.query(polygon=box(150, 200, 180, 240), classes=[2, 12], last_return=True)
distance, index = points.tree.query([[168, 246]], k=8)
ground, stats = select_ground(points)
surface = Surface(ground)
samples = profiles(surface, LineString([[150, 220], [180, 240]]))
rasters = surfaces(points, C.CollisionTile(2, 3).bbox, resolution=0.25)
```

## Cache and support

Each 500 m source is streamed once and partitioned into 62.5 m collision shards.
The structured `.npy` dtype stores float64 xyz, uint8 classification/return number/
return count, and uint16 intensity. **NPZ cannot be memory-mapped**: point caches
use NPY; small derived raster bundles use compressed NPZ. Unbuffered tiles map
source shards directly; buffered crops are persisted separately. Bounding boxes
are half-open. Polygon queries include boundary points. A query over multiple
tiles reads disjoint unbuffered shards, so overlapping buffers do not duplicate points.

Cache keys include source absolute path, size, nanosecond mtime, implementation
hash and origin/grid/dtype. Input checksum manifests remain authoritative; modifying
a source while preserving its size and timestamp requires removing its cache.
Source publication uses a process lock and atomic directory rename. Tile objects
and their lazy horizontal KD-trees are retained in a bounded 24-entry per-store LRU.
Missing source files fail explicitly; they are not represented as empty ground.

`benchmark` compares repeated LAZ decode/crop against fresh-store mmap reads and
forces all xyz pages to be touched with the same checksum. Both use the warm OS
file cache. First-time partitioning is reported separately from repeated access.

Ground selection uses class 2 and class 12 within 15 cm of a robust class-2 local
plane, requiring support within 2 m. `Surface.sample` retains W1-01's unbounded
default for compatibility; callers can set `max_distance` explicitly.
Ground rasters store observed-cell medians, fill holes only within 0.75 m, and
carry observed masks, points/m² density and confidence (decaying across filled
cells). Unsupported cells stay NaN. DSM excludes noise classes 7/18; nDSM subtracts
the ground; CHM uses vegetation classes 3/4/5 only. CHM is NaN without canopy returns.

## Profiles, detection and breaklines

Profiles reuse W1-01's gradient snap and trimmed side-plane fits. Planes extrapolate
to the same edge, removing road slope from the signed step. Sparse, ill-conditioned,
steep or noisy profiles are unsupported. `Profile` retains side heights, point
counts, residual MAD, cross slopes, signed step and confidence. The tuple-returning
`fit_profile` adapter is available for ground consumers.

Raster height jumps are detrended by the local median gradient, linked on grid
faces, then checked against raw-point profiles. Mapped curb profiles can refine
positions within 45 cm. Lidar-only additions exclude an 80 cm corridor around
mapped curbs, building interiors (+0.3 m) and water; they require at least 5 cm step,
confidence >= 0.25 and surviving measured polylines at least 2 m long. Unsupported
stations split polylines. No span is bridged across a rejected station.
Class-transition fragments shorter than 5 cm are discarded before export;
they cannot form a stable collision strip.
The AOI runner reserves 20% of returns by the same deterministic coordinate hash
as W1-01 before ground selection, profiling and edge detection. Reserved returns
remain available to independently evaluate a later mesh.

The W1-01 JSON-list interface (agreed on the coordination board) is:

```json
[{"object_id":"lidar:12:0", "centers_xz":[[10,20],[11,20]],
  "normals_xz":[[0,1],[0,1]], "side_heights":[[4,4.12],[4.01,4.13]],
  "kind":"curb", "relief_class":"curb", "provenance":"lidar",
  "confidence":[0.8,0.9], "step_m":[0.12,0.12],
  "upper_side":["plus_normal","plus_normal"],
  "lower_side":["minus_normal","minus_normal"]}]
```

All arrays have equal length >= 2; normals are unit x/z vectors. `side_heights`
is **minus-normal, plus-normal**. Positive normal `(-tangent_z,tangent_x)` is
map-right because engine z points south. `kind` uses ground's
curb/curb_dropped/crossing/quay/wall enum. Measured steps above 18 cm and
planter/retaining edges use `wall`, so ground preserves their rise and renders
their solid face. `relief_class` adds curb, kerb_drop, step,
ramp_edge, wall_planter_edge and quay. These classes are height/slope hypotheses,
not independent semantic verification. Quays are handed to structures by ground.

Confirmation coverage counts adjacent supported vector stations, including measured
flush/dropped sections; raised length is separately reported at >= 3 cm. Neither
is mesh-step coverage. Added lengths are measured on exported polylines. Step
percentiles are weighted by source station-interval length, including flush spans.
Small-object blob/PCA labels (pole, bollard, bench, low_wall, planter) are candidates
for later props, with low confidence to reflect semantic ambiguity.
Their `center_xyz` is the horizontal footprint center at ground elevation; `height`
is separate, so later prop assets can use a ground-level placement origin.

## Wall points

`turku3d.lidar.walls` streams the raw LAZ tiles once and keeps, per building wall, the points
in a window around the wall rectangle, in the wall's own frame: `u` along the wall from its left
edge seen from outside, `h` above the wall base, `d` signed distance from the plane (positive
outward), plus 8-bit RGB, class and returns. `walls_from_features` reads the buildings layer's
`facades.geojson`. It does not use the point cache, whose dtype has no colour.

```python
from turku3d.lidar import walls as W
walls = W.walls_from_features(features)
points = W.wall_points(walls, W.Window(inside=0.6, outside=0.6))  # {wall_id: records}
```

The point colours come from the nadir aerial imagery and are mostly the roof's or the
ground's on walls; see `docs/reports/textures-lidar-facade-color.md`.

## Experimental mesh comparison

Curbs were deferred from the current playable-block release on the coordination
board. The toolkit does not require the current ground layer to consume its output.
The historical W1-01 curb builder can consume `added-breaklines.json` for next-iteration
experiments. Comparison operates directly on saved collision NPZ and ground QA:

```sh
bash pipeline/env/run.sh python -m turku3d.lidar.compare \
  --before "$TURKU3D_WORK/build/layers/ground-before" \
  --before-qa "$TURKU3D_WORK/reports/ground-before/ground-qa.json" \
  --after "$TURKU3D_WORK/build/layers/ground" \
  --after-qa "$TURKU3D_WORK/reports/ground/ground-qa.json" \
  --output "$TURKU3D_WORK/reports/lidar/mesh-comparison.json"
```

The comparison uses identical held-out stations and length weights for both meshes.
It reports mapped and added relief separately; missing mesh profiles count as failures.
Added locations are sampled against baseline collision triangles. The helper has no
dependency on ground builder modules and remains usable after that builder changes.
