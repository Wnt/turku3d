# Triangle geometry audit

Run on existing uncompressed stage GLBs or shipped meshopt GLBs, without a GPU,
network requests, textures, or tile builds. Requires Python/numpy/Pillow and the
generator's existing Node glTF tooling (`TURKU3D_GLTF_TOOLS`, default
`~/turku3d/tools/npm`). External geometry buffers must accompany shipped GLBs.

```sh
PYTHONPATH=pipeline python3 -m turku3d.geometry_qa TILE/vis.glb --out /tmp/geometry-qa
PYTHONPATH=pipeline python3 -m turku3d.geometry_qa --sweep CACHE --out /tmp/city-qa
```

Each product gets a JSON report with source SHA256, counts, triangle identifiers,
engine coordinates, EPSG:3877 E/N/height, thresholds, coverage and unchecked
categories; its PNG is north-up with red high-severity and yellow warning dots.
`summary.json` and stdout provide aggregate counts. Exit 0 means no findings, 1
means findings, 2 means an input failed (the remaining products are still audited).
Choose the farm's current product root for a sweep; historical revisions and stage
layers are separate products, not one assembled tile. The tool never fetches tiles.

Current checks: nonfinite/extreme heights, road degeneracy, reversed winding,
slope magnitude over 25%, long slivers, and positive-area coplanar overlap between
different source objects/material primitives within 3 mm. It respects packaged
source-object index ranges and transforms. Mere edge contact is clean. Geometry
overlap is a potential z-fighting cause: runtime shader displacement, depth bias,
alpha cutouts, hidden/internal faces and material visibility can make it harmless.
Slope uses asphalt/cobblestone/deck attribution, not a lane-direction model: steep
material-labelled terrain is a candidate, not confirmed impossible road geometry.

Not yet checked: lane-direction cross-slope, adjacent height steps, boundary holes
and saw-teeth, road/terrain penetration, bridge clearance/approaches and detached
railings/girders. GPU-instanced props and vegetation are excluded. A clean report
does not certify these categories. Thresholds should be calibrated against measured
street geometry before promoting this audit to a per-build failure gate.
