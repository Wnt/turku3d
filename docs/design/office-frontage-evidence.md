# Petrelius office frontage detail study

Building: `103295893K`, Läntinen Rantakatu 15. This record describes authored
geometry and visual observations; it does not assert survey accuracy.

## References and confidence

The [Petrelius article](https://www.jalkipeli.net/petreliuksen-talo-on-taynna-hienoja-yksityiskohtia/)
contains a restored oblique frontage, an opposite-river context view and a
courtyard view. The article is dated 2025-02-01, but image capture dates are not
established. Its restored cream siding and finished trim are more useful for
appearance than the stripped 2019 panorama. Upload paths are not capture dates.
Reference pixels remain private and are not published with the game.

The restored oblique and opposite-river views agree on three architectural
sections: flat projecting crowns on the two three-window end groups, triangular
pediments on the central four-window group, and a simpler grey four-window wing
at the southwest. The courtyard image supports deep timber cornice/bracket
construction but is not used to place street openings. Black standing-seam roof,
red brick chimneys, horizontal main-storey siding and vertical lower panels are
observations retained for later work, not new changes in this slice.

## Implemented addition

`scene/facade_details.py` supplies ten shallow projecting crowns, including four
triangular pediments. Each includes fascia, stepped moulding courses, small
dentils and paired jamb-side brackets. Projection 0.24m and rise 0.39/0.64m are
visual priors with approximately ±0.10m uncertainty, not measured dimensions.
The grey four-bay wing receives no ornate crowns. Construction barriers and
other temporary objects in the reference are not part of the building asset.

`office_detail_anchors.json` records window-head positions derived from the
existing game-owned GLB's UV coordinates and facade-kit-v6 opening program.
The three retained grids are 7.232m / 3 bays, 11.033m / 4 bays and 7.184m / 3 bays.
An affine UV-to-position fit had maximum residual below 1.2e-14m. This only proves
attachment to the authored program; it does not prove the source grid matches a
survey. The exact source GLB hash is recorded in the anchor file.

The addition preserves the facade program, textures and opening dimensions. Its
paint reuses the existing `_KIT_TRIM` linear colour; reference photographs are
not colour calibrated. All ten meshes are passable, total 2,288 triangles, and
may be batched by the compiler under one material. Existing building collision
continues to own contact. Tests verify outward winding, glazing clearance,
bounded projection and exclusion of the grey wing.

## Visible acceptance

This asset is an input until placed in normal city.lab gameplay and reviewed in
a matched pair. Use the office baseline camera position/quaternion and FOV, fixed
720×480 internal rendering at 1440×960 output, clear weather and the recorded
2026-06-15 14:00 +03:00 time. Record camera residuals, source/release IDs and any
unavoidable differences. A native-resolution beauty view is separate evidence.

## Roof and restored appearance input

The next input uses the same restored oblique, the actual courtyard building
view and the opposite-river view. They agree on dark standing-seam sheet metal,
red brick chimneys with stepped caps, small pointed front dormers, deep repeated
eave brackets, cream horizontal boards and vertical lower panels. The article's
statue photograph is not a building reference; the private filename was corrected
to make that distinction explicit. These photographs are not calibrated colour
measurements, and their capture dates remain unknown.

The retained authored roof has a main front plane with approximately
`normal=(0.22857,0.88348,0.40892)`, eaves around H10.64–10.71m and ridge H13.69m.
`office_roof_details.json` records a rounded polygon and three cornice attachment
spans derived from that game-owned GLB. The roof shell, UVs and slope are retained.
`roof_details.py` adds three projecting cornice runs with repeated timber corbels,
six capped brick chimneys, two pointed glazed dormers and 0.55m-pitch folded seams.
There are 40 passable mesh records and 6,602 triangles. Existing building collision
continues to own contact. The families can be reused with explicit metric anchors.

Chimney authoring centres/top heights are rounded from six compact last-return
class 1/6 clusters over this roof, with 18–25 samples per cluster above H14m.
Their tops span H15.14–15.77m. This supports placement and silhouette height;
shaft section, brick course size, flashing and rain-hood construction remain
visual priors. Approximate placement uncertainty is ±0.25m. The rear southwest
pair is less directly visible in the photographs than the four main chimneys.
Raw samples stay private; only derived authoring parameters are included.

Two calibrated 2019 panorama poses were used for dormer geometry, not restoration
colour. Manually matched roof-apex rays give approximately
`(175.684,12.746,248.718)` and `(168.913,12.432,252.463)` in engine coordinates,
with closest-ray residuals 0.239m and 0.076m respectively. Source registration and
manual feature picks are not exact. The shared authored apex H12.60m and inferred
1.56m rise / 1.56m width use ±0.4m uncertainty. Their fronts lie about 0.67m inside
the retained eave line. Roof intersections hide the unexposed rear of each dormer.
Corbel depth 0.46m and 0.80m repetition are visual priors, approximately ±0.1m.

`office_appearance.mjs` changes only four paint attributes and the roof material's
tint/roughness. It rejects a source that differs from the approved authored GLB,
retains the original 207,752-byte binary prefix, all geometry/UV/layout bindings
and all four external image URIs, and appends replacement colour attributes.
The southwest grey wing is explicitly excluded by its authored corner plane.
Warm cream/brown linear RGB values and the dark roof tint are appearance priors,
not sampled colour truth. The original timber material program and texture
library remain attached. Painted metal uses dielectric metalness zero.

To prepare the appearance input:

```sh
node pipeline/turku3d/scene/office_appearance.mjs INPUT_OFFICE.glb OUTPUT_OFFICE.glb
```

The producer must retain the original approved source hash in a scene source
record `building:103295893K:authored`, so the existing ten crowns can stay tied to
the unchanged authored grid after the output GLB hash changes. Attach
`office_roof_details()` and `ROOF_DETAIL_MATERIALS`; replace the existing ornament
material with `OFFICE_RESTORED_ORNAMENT_MATERIAL` only when the adapted GLB is used.
Never add a duplicate material ID. This input is not a visible accepted iteration
until the producer places it and matched normal-gameplay images are inspected.

Dedicated roof-before views were captured from normal gameplay on `dfdccafa` at
720×480 internal / 1440×960 output, FOV70, June 15 14:00 clear. Their actual camera
transforms are stored privately in the hero-before manifest. These supplement
the original FOV60 pair, whose tighter framing crops much of the roofline.
