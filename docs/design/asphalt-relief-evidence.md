# Prepared asphalt relief

The centre pilot uses the existing `asphalt_worn` PBR library. Its declared
physical repeat is 2 m (`textures/library/ground.json`), reproduced by the
prepared road UV scale `[0.5, 0.5]`. The promenade is already classified as
asphalt. The pale, uniform appearance is not a footway/material-name error.

The original library receipt (`docs/reports/ground-textures.json`) records
Sobel-on-luminance normal generation. This is an appearance prior, not a measured
height field. The shipped normal map
`494d45f257b0e401d341ad912f41ad17ec242abc988d7af4ebbe00ea191b8a6d`
has median tangent-space tilt 30.67 degrees and 90th percentile 47.18 degrees.
A normal strength of 0.25 gives 8.43 and 15.10 degrees respectively. The albedo
and ORM remain unchanged; ORM mean roughness is 0.925.

A native M1 comparison on 2026-11-12 used public renderer `24e3b4c1` and scene
`f6d884bbffa7dbd2400737b72fe5198e494fb38211dabfc72edf07f374c916d6`.
Two fixed world cameras (office paving and Market Hall north) each captured
baseline, reduced relief, 1 m repeat, and both changes. All eight images used
1440 × 960, medium quality, clear weather and the frozen instant
2026-06-15T11:00:00Z. Camera residual was zero. No GPU, runtime, HTTP or resource
errors occurred, and position/index buffers and collision records retained their
hashes. Private evidence is in the scene worker's
`work/reports/ground-material-ab/manifest.json`; no reference pixels are tracked.

Select only the reduced relief. It modestly softens the coarse granular shading
and better matches the smoother registered 2019 office asphalt reference. The
0.25 value remains an optical prior, not surveyed roughness. Reject the 1 m
repeat: it changes the visible grain more strongly, but the reference does not
justify overriding the library's metric scale. The current high-contrast,
uniform albedo remains a separate limitation; this change does not claim to
restore observed cracks, repairs or colour variation.

The office recipe changes only `road.normalScale`. All map hashes, mean colour,
roughness, UVs, geometry, collision and lighting bakes remain authoritative.
Other scene recipes are unchanged. The optional material field also preserves
an explicit source glTF `normalTexture.scale`; absent values retain strength 1.
