# Prepared street-light receivers

Run `turku3d.scene.night_lighting.bake_scene(scene)` after final geometry and
ambient preparation, before the scene release hash. It uses the existing Rapier
dependency through `night_bake.mjs`; Node 24+ with `--experimental-wasm-modules`
is required. The wrapper has a 120-second offline limit. No bake runs in gameplay.

Each rendered opaque native mesh receives `nightIrradiance`: finite nonnegative
RGB floats, one triple per position. Values are full-night outgoing Lambert
radiance per unit diffuse albedo, including the receiver cosine and `1/pi`.
Zero triples mean the receiver has no direct source contribution after range, orientation and visibility tests. They
must remain present so the runtime does not substitute its approximate field.
Water, lamp glass and imported rendering assets retain their existing paths.

Occlusion uses every explicit solid mesh, including collision-only building
shells, retaining walls, bridge bodies and fixture posts. Mesh-local Float32
colliders preserve small edges. Receiver rays start 10 mm outside the prepared
face; the emitted optical centre already includes its producer-owned clearance
and is never offset again. There is no receiver-base-height mask.

Point sources use their authored position, candela prior, linear RGB and
spherical cutoff. A reflected canopy may specify an `aperture` rectangle with
world position, orthonormal `normal`/`axisU`, metre `size` and central
`cutoutRadius`. Its other axis is `normal × axisU`. Sixteen midpoint samples
approximate a diffuse downward aperture; samples inside the pole cutout are
excluded. Their weights sum to one, preserving total on-axis intensity as
quadrature resolution changes. These are visual photometric priors supported
by fixture geometry, not surveyed bulb technology or measured illuminance.

Runtime geometry packs the values into the existing `_night_irradiance`
attribute and marks the prepared street-light receiver explicitly. Baked and
unbaked parts of a material use separate draws. Baked direct diffuse replaces
both the approximate lamp field and nearby point diffuse; the shared night
uniform and material diffuse contribution supply time and pigment. Sun,
ambient/GTAO, vehicle lights and point specular remain independent. Original
uplight corona positions remain unchanged; nearby point slots use the effective
aperture centre. Their specular and illumination on moving/unbaked objects are
still unshadowed approximations with the existing fixed slot budget.
