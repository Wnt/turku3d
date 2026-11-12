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

## Binder-dominant albedo repair

A second comparison establishes that source albedo contrast dominates the gravel
appearance. The existing fresh-asphalt library alternative is darker but has
even greater luminance variation; it is not a suitable repair. The cached fused
orthophoto still contains trees, cars and shadows and is also rejected as albedo.

The built-in image tool edited only the existing generated worn-asphalt patch,
with textual material observations from the registered office panorama. No city
reference-image pixels were supplied. The exact prompt, input/output hashes and
licence are pinned in `asphalt_material_source.json`. The result emphasizes a
continuous weathered binder with nearly flush fine aggregate. Its mean colour is
a visual prior, not a calibrated measurement. It introduces no cracks, paint,
repair outlines or street-specific features.

A second native comparison on renderer `c1213324`, scene `ed5d1a7c`, used the same
two cameras, frozen time, weather and resolution. All variants retained the
2 m UV repeat, 0.25 normal strength and original ORM. A shader-only diagnostic
matched the original albedo's linear mean to the repaired map; this multiplier
is **not** adopted. In a fixed unobstructed paving region the mean-matched old
and repaired images average 135.98 and 136.72 display levels, while luminance
standard deviation falls from 4.67 to 2.20. The old material still looks like
exposed gravel at the matched mean; the repair reads as compacted asphalt.
Select the repair for the centre recipe only.

Private comparison receipt:
`work/reports/ground-material-repair-ab/manifest.json`. All six camera residuals
are zero, the frozen time is identical, and geometry/collision hashes are
unchanged. GPU, runtime and resource error lists are empty. The preview sampled
the decoded final KTX image through a temporary native texture; final compressed
asset loading remains part of the deployed slice check.

The immutable generated PNG is in the canonical prepared asset store as
`210ef8a9b9edf0dd789c13310165a60770eee168da0ec110a1621395b87ec19e.png`.
`python -m turku3d.scene.asphalt_material INPUT.png OUTPUT_DIR` reproduces the
1024-square, block-aligned sRGB KTX. Packaging converts format/size only: it does
not repaint or normalize the generated raster. Normal/ORM maps remain the
existing library products, so their small-scale relief remains an appearance
prior rather than a measured or exactly correlated height field.
