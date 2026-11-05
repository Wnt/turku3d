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
| L0 bbox (excl. whip) | x -2.48 .. 2.13 (rear tube bumper .. soft shackle), width 2.562, roof panel 1.91, light bar 1.98, whip tip 3.12 |
| triangles L0 / L1 / L2 | 126,260 / 27,036 / 5,938 (body alone 29,262 / 4,870 / 3,342) |
| raw primitives L0 / L1 / L2 | 123 / 121 / 44 (before the per-material merge) |
| sha256 (raw) | 48a93ad6904aeba6d10dc6206f96d47f6daf6e5af9e1dece4a57ac19dae6f551 |

Node contract (SPEC 2.2): `trophy4400` root; `body` (`body_L0/L1/L2`); per corner `uca-*`, `lca-*`,
`upright-*` > `wheel-*` (tyre/rim `_L0/_L1/_L2`), `tierod-*`, `coil-*-body/-shaft/-spring`,
`bypass-*-body/-shaft`, `cv-*-inner/-shaft/-outer`; `rack-front-slider`, `rack-rear-slider`,
`steer-wheel`, `spare`, `antenna`, lamp/seat/exit empties and 64 `hp-*` hardpoints under `hardpoints`.
Rig nodes are empties at the pivots; axial nodes point Blender +Z (glTF +Y) from top/inner to
bottom/outer. Lamp lenses use `led-optics` / `tail-lens`.

Round 4 body (2026-11-05): compact, tall-cabbed Ultra4 shape. Planar outline panels (Bezier outlines
filled and solidified) for the side skins, cab B-panels and a wide flat hood; deep arch shells with
rolled lips and dark liners over all four tyres; an orange roof panel at 1.91 m with the light bar on
its leading edge; low round LEDs either side of the winch in a small tube bumper; a dark V skid plate
with the black-on-white 19; an upright spare leaning forward behind a tall angled rear panel; window
nets, mirrors, A-pillar cube lights, fender pods, seats with 5-point harnesses. Livery is logo-free:
orange with a dark-grey upper cab band, a white pinstripe and a dark rocker band. A-arms are heavier
dark tubes with gusset plates; wheel centres are dark gunmetal (material `anod-blue` keeps its old
name but is no longer blue).
