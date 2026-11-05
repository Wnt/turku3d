# trophy4400: #19-style 4400 car (raw Blender export)

Our own model of a four-wheel-steer, fully independent (IFS + IRS) 4400-class car in the style of the
#19 car, modelled from scratch in Blender 5.2 through the blenderwright 3.1.0 MCP tools (parameterised
tool calls only; no Blender Python). No downloaded mesh, sponsor logo, brand mark or photograph is
included. The only texture is the generated "19" plate (`livery/plate19.svg`, our own stroke numerals,
rasterised with rsvg-convert). Copyright 2026 Jonni Madekivi; MIT, as the repository.

Base model: none. A Sketchfab search (ultra4, king of the hammers, trophy truck, tube chassis, rock
bouncer, desert/offroad buggy, sand rail; downloadable, CC BY/CC0) found only pickup-bodied trophy
trucks or chassis under the Sketchfab Standard licence, none close to this car, so nothing was imported.

Source (local, not in git): `~/turku3d/data/assets-src/trophy4400/trophy4400.blend`; the tool-call
generators are in `~/turku3d/loops/trophy4400/model/gen/` and the previews in
`~/turku3d/loops/trophy4400/model/previews/round-N/`.

`trophy4400.glb` is the **raw** export (`export_apply`, no Draco/WebP, Y-up, +X forward, left = -Z,
ground origin, x = 0 at mid-wheelbase). `scripts/prepare-trophy4400.mjs` (SPEC section 2.6: single-sided
materials, merge body by material, meshopt, KTX2, `hardpoints.json`) is not written yet.

| quantity | value |
|---|---|
| wheelbase / track (wheel-* origins) | 3.150 / 2.180 m |
| tyre OD x width | 1.069 x 0.382 m, 36 pitches, beadlock with 24 bolts |
| L0 bbox (excl. whip) | x -2.463 .. 2.134, width 2.562, roof 1.90, whip tip 3.05 |
| triangles L0 / L1 / L2 | 123,648 / 26,626 / 3,871 |
| raw primitives L0 / L1 / L2 | 118 / 116 / 40 (before the per-material merge) |
| sha256 (raw) | 2554a8d1d325987d5a0843b60e975f027d6181e0c5819b951d0dba97df1ae491 |

Node contract (SPEC 2.2): `trophy4400` root; `body` (`body_L0/L1/L2`); per corner `uca-*`, `lca-*`,
`upright-*` > `wheel-*` (tyre/rim `_L0/_L1/_L2`), `tierod-*`, `coil-*-body/-shaft/-spring`,
`bypass-*-body/-shaft`, `cv-*-inner/-shaft/-outer`; `rack-front-slider`, `rack-rear-slider`,
`steer-wheel`, `spare`, `antenna`, lamp/seat/exit empties and 64 `hp-*` hardpoints under `hardpoints`.
Rig nodes are empties at the pivots; axial nodes point Blender +Z (glTF +Y) from top/inner to
bottom/outer. Lamp lenses use `led-optics` / `tail-lens`.
