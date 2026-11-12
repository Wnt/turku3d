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

The office recipe uses 45 cd / 27 m for flat canopies and 90 cd / 42 m for
crooks. These remain inferred visual priors. The cutoff radii were increased
by one common factor of 1.5 after tracing the fixed night-quay receivers on
release `462a6331`: at `[153.538, 1.232, 280.236]` all sixteen nearest
canopy aperture samples were visible, but its original 18 m cutoff removed
82% of that lamp's uncut contribution. At `[157.430, 1.581, 275.913]`, actual
retaining walls blocked both nearby canopies; a visible crook's original
28 m cutoff removed 89% of its contribution. These separate cutoff effects
from genuine multi-level occlusion. Intensities, optical positions, aperture
quadrature, geometry, visibility rays and ambient illumination are unchanged.
The change requires matched night-image review; it is not a claim of measured
street illuminance or elimination of true shadows.

## Observed under-crest quay lights

`scene/quay_light_source.py` freezes the existing `coarse/quay_lighting.py`
eligibility: named Aurajoki bridge reach, mapped pedestrian corridors, measured
walks, water-facing lower shoreline faces and crests 0.4–3 m above water. Its
OSM/policy hashes become one recipe input. An absent corridor source produces
no bars. Fixture inventories and exact optical output remain inferred; the
private Vastavalo 399195/554993 references establish the visible cue, not lux.

The adapter retains each original solid wall as collision authority. Qualified
visible faces are subdivided in place with the exact original metric UV map.
The existing observed product supplies 45 mm tall bars, 25 mm outward clearance,
35 mm distance below the crest and a quadratic masonry wash limited to 0.9 m
below it. Bar materials use explicit `nightEmission`; the wall-only wash adds
to that wall's prepared receiver array after the street bake. Both use the
shared night factor and existing renderer-relative source intensities.

There are no new point lights, gameplay passes or changes to road receivers.
This restores an omitted night cue. It does not brighten the lower road, remove
real retaining-wall shadows or add general multibounce illumination. Original
collision hashes, unrelated receiver arrays and same-pose day/night imagery
are the acceptance checks.
