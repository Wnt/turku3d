# Reusable bench timber finish

The mapped promenade-bench family retains its exact vertices, indices, dimensions,
yaw and support contacts. `scene.timber_surface` supplies only local metric UVs and
three original procedural maps. The existing brown palette remains linear RGB
`[0.18, 0.09, 0.035]`; zero-centred grain varies that linear base before sRGB encoding.
The textured material uses white multiplication so the palette is not applied twice.

A repeating 2.0 × 0.5 m field uses restrained longitudinal fibres, small roughness
variation and at most 75 micrometres of relief. These are material priors, not
measured wood species, varnish, weathering or pigment. There are no reference pixels,
new fixture placements, artificial baked shadows or geometry changes. The roughness
map is nonmetallic and its occlusion channel remains one.

`bench_uvs(piece, position, yaw)` derives coordinates in the fixture's own frame.
Moving or rotating a bench therefore leaves the grain along its slats. Top and side
faces use their respective metric plane; split box-face vertices are required. The
function rejects conflicting projections instead of silently stretching a shared
corner. It returns UVs without mutating the source mesh.

Generate outside Git:

```sh
source ../sandbox.env
PYTHONPATH=pipeline ~/turku3d/envs/pipeline/bin/python -m turku3d.scene.timber_surface \
  "$TURKU3D_WORK/bench-timber"
```

The output `material.json` records the prepared material and immutable KTX2 hashes.
All three base extents are 1024 × 256 and checked in the encoded KTX2 headers. Copy
these products through the canonical external asset store when integrating; no
PNG/KTX payload belongs in Git. The producer must pass UVs only for mapped furniture
pieces with material `wood` and retain every other material and collision product.

`fixtures.place_furniture` supplies those UVs to the existing triangle adapter.
The office recipe pins the three maps with `uvScale: [1, 1]`, since the family UVs
already encode the metric repeat. The material ID stays `wood`; it does not claim
an unsupported prepared weather-surface category. The production-adapter test
compares every position, index, normal, entity, collision and support field against
the prior projection for a mapped bench and bin; only wood UVs may differ.

The targeted tests cover GPU-compatible dimensions, palette/roughness constraints,
fibre direction, and UV invariance under an actual bench's rotation/translation:

```sh
PYTHONPATH=pipeline ~/turku3d/envs/pipeline/bin/python -m unittest discover \
  -s pipeline/tests -p test_timber_surface.py -v
```

This input alone is not a visible iteration. Acceptance requires a correctly placed
bench in ordinary gameplay, compared against the dedicated native-resolution before
view with the same camera, time, weather and render resolution. Current engine/surface
work and its evidence must finish before changing that material recipe.
