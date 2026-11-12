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
