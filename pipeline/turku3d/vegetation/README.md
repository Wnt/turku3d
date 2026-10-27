# Vegetation layer

```sh
source ../sandbox.env
bash pipeline/env/run.sh python -m turku3d.vegetation                  # every listed tile; only stale ones rebuild
bash pipeline/env/run.sh python -m turku3d.vegetation --tiles t0_3_0   # one tile
.venv/bin/python -m pytest pipeline/tests/test_vegetation.py -q
scripts/dev/gate
```

The layer is built per listed L0 tile ([`turku3d.tile_layer`](../tile_layer.py), `layer.py`).
A tile reads the tree register of its own input area (`config.source_area`: work-3x3 while it
holds the tile, else `tile-<id>`) and the lidar under the tile plus `READ_MARGIN` (12 m), so
trunks near its edges compete for canopy returns as in one large build, and it keeps the trees
whose trunk lies in the tile: its `t0_i_j.vis.glb`, the trunk cylinders of its collision tiles
and `t0_i_j.records.json`. Its key covers the four register layers, the LAZ files, the assets,
the placement context (`placement.inputs.context_key`: collision within 20 m, earlier
inventories within 30 m) and the code, so a tile added with `make tile-add` builds only that
tile. Trees of unlisted neighbours (the old 50 m margin) are no longer built. `make refresh`
runs this layer after props and before traffic_static. Each LAZ file is read once and reduced
to its class-2 ground (first return per 0.5 m cell) and class-5 canopy (highest return per
0.5 m cell), cached by content in `$TURKU3D_WORK/cache/vegetation`; a tile crops the cells it
needs. `trees.json` is combined from every listed tile's records.

`GIS_Puut` wins over mapped `kanta_Lehtipuu` / `kanta_Havupuu` points within 2 m.
Register coordinates are never moved. `kanta_Lehtipuurivi` is sampled at 8 m and
requires lidar support, because a line does not identify individual trunk locations.
One observed ground point per 0.5 m cell limits memory. Ground height is the median
of six nearby samples; points without ground support within 12 m are omitted.

The canopy is reduced to the highest return per 0.5 m cell before measurement,
so repeated vertical returns do not overweight dense parts of a crown.
A 1 m CHM selects separated maxima above 6 m with at least 20 occupied canopy cells
within 2.5 m for lidar-only additions. Cells above 2.5 m are assigned to the
nearest tree within 10 m. With at least 12 cells, height is their 98th percentile
above local ground and crown radius is the 90th percentile horizontal distance.
Unmeasured register/mapped points retain an explicit 10 m height / 3 m crown
fallback. `trees.json` lists every coordinate, source, species, measurement support,
fallback ID and per-tile budget. It is hash-linked by `MANIFEST.params.inventory_sha256`.
Sparse/new trees and different survey epochs therefore remain auditable.

Four [Kenney CC0 models](assets/README.md) are scaled to measured height/radius.
Autumn deciduous crowns and green conifers use rough, nonmetallic materials.
`EXT_mesh_gpu_instancing` batches each variant/material with deterministic yaw;
`instance_object_ids` maps instance index to inventory ID. Budgets count actual
instances: triangles per color pass, prototype triangles, draws per color pass,
and uncompressed source bytes. Shadow/depth/postprocessing passes cost extra.
Trunk-only cylinders use material 26, height min(0.55 × tree height, 8 m), and
estimated radius clamp(0.018 × height, 0.09, 0.55 m). Canopies never collide.

The assembler preserves instancing through lossless meshopt, checks decoded world
positions within 2 mm, counts instance triangles and expands tileset bounds to all
instances. Its higher-level terrain/roof policy currently omits vegetation.

First-version limitations: stylized low-poly crowns, approximate species mapping,
nearest-seed crown segmentation in touching crowns, inferred trunk diameters,
no building-mask rejection of lidar-only candidates, and coarse row sampling.
Adopt the shared `turku3d.lidar.TileStore` in `load_points` when that module lands;
the current branch deliberately has no dependency on an unlanded toolkit.

## Shared placement acceptance

After canopy measurement, the [shared validator](../placement/README.md) snaps trunk
bases to published ground/structures triangles and checks grass/soil/tree-pit semantics,
water, missing bridge decks, buildings, access and other object bases. Corrections are
limited to 2 m; omitted trees and original/final positions are retained in JSON/HTML
orthophoto reports under `$TURKU3D_WORK/reports/placement/vegetation/<tile>`. Residual hard
violations fail publication. Ground and building layers must exist before this build.
