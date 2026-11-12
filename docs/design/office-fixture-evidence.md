# Office promenade fixture evidence

These are family proportions and placement associations, not complete surveyed
fixture models. Reference pixels and the WFS response remain private.

Two June 2019 Google panoramas (`hOH16IE5o9B6ar0x7Cm-9w` and
`9YcdVIRiQEgvodxtjAX71A`) were projected using the repository's calibrated
`panorama_pose` convention, including pitch, roll and grid convergence. The
canopy beside the office is a wide rectangular reflector with a lower uplight,
not a generic T-arm. A restored Petrelius oblique also shows this silhouette.

`CityFurniture_56200764` is matched in two views. Other nearby 56200762/63/65/66/67
points form the same line, but repeated canopy type remains an inference until
each is individually checked. Canopy long axis runs along the street; family
yaw ≈ 0.52 rad is an orientation prior with approximately ±0.15 rad uncertainty.

`CityFurniture_19460502` is a tall spiral-crook with a suspended bell, clearly
visible from both panoramas. `19460503` is partly occluded by a tree but has a
consistent silhouette. `13283222` is a distant same-family inference. None of
these associations calls for the four-pane heritage lantern family.

The initial 5.28 m crook height was disproved before installation. Manual pixel
observations in the two calibrated views put the spiral top at absolute H 12.42 m
and 13.07 m, and bell centre at 10.99 m and 11.77 m. Horizontal residuals of 1–1.5 m
limit precision: source pose, registration, curve offset and manual annotation
all contribute. Independent class 2/12 lidar plane fits around the first two
poles give local ground about 2.13 m. A 10.60 m family with emitter 9.18 m above its
support therefore matches the observed scale within an explicit ±1 m height
uncertainty. Its open spiral and smaller counter-scroll replace the short hook.

The crook extends along local -X. Image silhouette and street bearing suggest it
points toward the river, requiring yaw ≈ 2.094 rad (moderate confidence, ±0.15 rad).
The imported WFS geometry is only a named Point; its elevation is not explicitly
labelled a pole-base attribute. Final terrain support and measured ground remain
the authority for attachment. `supportRadiusM` describes the actual contact disc
(0.105 m crook, 0.10 m canopy), independently of the elevated head's wider bounds.

## Reflected canopy aperture

The authored canopy has a rectangular reflector underside at local Y5.56m,
1.20m along X by 0.84m along Z. Its continuous pole intersects the middle with
radius0.046m; the raw uplight top remains at Y4.85m. An isotropic point placed
at that raw source therefore starts inside the post. The effective reflected
source is explicitly a downward Lambertian approximation over the underside,
with a circular pole cutout. These dimensions inherit the photograph-informed
family priors and ±0.8m height uncertainty; no photometric survey is claimed.

`FLAT_CANOPY_LAMP.reflectedAperture` records the local rectangle, downward
normal and X tangent. The producer transforms these once into world space and
moves the optical sampling plane 0.005m along its normal, outside the reflector.
That epsilon is numerical separation, not a measured physical offset. Static
quadrature omits the cutout and still tests the actual pole as an occluder.
The original emitter offset remains available for the visible uplight corona.
