# Buildings → facade textures contract (v1)

Producer: `turku3d.buildings` (W1-03). Consumer: facade texturing (W1-09), then the assembler (W1-04).
The authoritative list is `$TURKU3D_WORK/build/layers/buildings/facades.geojson`.
It is a GeoJSON FeatureCollection with `schema_version: 1`, `turku3d_coords: "en"`,
and a declared EPSG:3877 CRS. These are projected metre coordinates, **not RFC 7946 lon/lat**.
Each feature is one planar wall polygon's horizontal baseline, as a two-point LineString.
The corresponding full polygon is in the layer GLB. Holes in wall polygons remain holes.

## Required properties

| Field | Type / units | Meaning |
|---|---|---|
| `layer` | string | `buildings` |
| `object_id` | string | `<building_id>/facade/<facade_index>`; also the feature ID and GLB node name |
| `building_id` | string | Kantakartta registry ID; source-feature hash if absent; disjoint parts carry a stable suffix |
| `facade_index` | integer ≥ 0 | Stable order described below |
| `base_height` | metres N2000 | Minimum height of this wall polygon |
| `top_height` | metres N2000 | Maximum height, including gables; not necessarily a rectangular facade |
| `normal` | array of three numbers | Outward unit normal in engine x/y/z axes |
| `width_m` | positive metres | Baseline length and maximum UV u |
| `street_facing` | boolean | Nearest street centreline within 60 m is in the outward hemisphere (cosine > .35), with no intervening building footprint |
| `material_key` | string | Library fallback material key |

GLB node extras repeat these fields. The assembler preserves them when binding
`textures/facades/<building_id>/<facade_index>.ktx2` and its JSON sidecar.
Sloping roof and ground nodes use `surface_type`, `building_id`, `object_id`, `layer`,
and `material_key`; they have no `facade_index`.

## Ordering and UVs

Footprint exterior edges are visited from the **source start vertex in source ring order**.
Courtyard rings follow, sorted by their start coordinate. Each planar wall polygon is
assigned to the closest edge; polygons on that edge sort by along-edge midpoint,
base height, then horizontal midpoint. Indexing starts at zero. This does not depend
on CityGML surface order or triangulation order. A changed footprint or selected
roof model may change indices: consumers must use the manifest code/input hashes
to invalidate generated bindings, rather than keeping an index across geometry revisions.
Roof step walls also receive indices after matching to the closest footprint edge.

The baseline starts at the wall's lowest projected u along that footprint edge.
Facade UVs are **metres**, `u = distance along baseline`, `v = H − base_height`.
They are not normalised to [0,1]. Texture binding converts to texture dimensions.
Roof UVs are planar E/N metres relative to the configured world origin:
`u = E − ORIGIN_E`, `v = N − ORIGIN_N`. The manifest records that UV origin.
Layer GLB positions are world-local engine coordinates with an identity root transform.

## Materials and provenance

Facade fallbacks: `facade_brick`, `facade_wood`, `facade_concrete`, `facade_plaster`
from kantakartta facade material. Roof classes: `roof_tile_red`, `roof_metal_green`,
`roof_metal_dark`, `roof_metal_gray`, from median RGB inside the footprint in the
2022 true ortho. These are colour classes, not claims about observed construction
materials. `metrics.json` records median RGB, sample count, OSM levels/roof shape,
entrances, city model match, candidate errors and final post-snap lidar residuals.
Texture assets are bound by the assembler; no placeholder raster is generated here.

`street_facing` is a geometric prioritisation hint, not proof that a pano has an
unoccluded view. W1-09 still selects and checks its reference photos.
