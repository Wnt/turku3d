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
| L0 bbox (excl. whip) | x -2.48 .. 2.11 (rear tube bumper / spare .. front tyre), width 2.562, body width 1.76 (nose) / 1.55 (cab), rocker 0.62, roof panel 1.90, light bar 1.955 |
| triangles L0 / L1 / L2 | 125,812 / 26,842 / 3,740 (body alone 28,814 / 4,676 / 1,144) |
| raw primitives (all LODs) | 223 (before the per-material merge) |
| sha256 (raw) | 393dc011336407ee97135a239e31dd47acf1c10252ad8768e4223e2c56c5e452 |

Node contract (SPEC 2.2): `trophy4400` root; `body` (`body_L0/L1/L2`); per corner `uca-*`, `lca-*`,
`upright-*` > `wheel-*` (tyre/rim `_L0/_L1/_L2`), `tierod-*`, `coil-*-body/-shaft/-spring`,
`bypass-*-body/-shaft`, `cv-*-inner/-shaft/-outer`; `rack-front-slider`, `rack-rear-slider`,
`steer-wheel`, `spare`, `antenna`, lamp/seat/exit empties and 64 `hp-*` hardpoints under `hardpoints`.
Rig nodes are empties at the pivots; axial nodes point Blender +Z (glTF +Y) from top/inner to
bottom/outer. Lamp lenses use `led-optics` / `tail-lens`.

Round 5 body (2026-11-05): narrow, high tube-chassis 4400 buggy. The body sits between the tyres
(cab sides at +-0.76 m, front flare strips at +-0.88 m against a 0.90 m tyre inner face) with the rocker
at 0.62 m, above the hub line, so the long A-arms, coilovers, tie rods and axles stay exposed. Thin,
crisp flat flare strips sweep up from a sharp front corner over open dark wheelhouses; the fender tops
slope down outboard. A short faceted nose box (flat top with vent, angled outer front faces) carries two
big round LEDs in its outer corners inside a proud tube hoop, with the winch recessed under the nose
and a tall narrow dark V skid with the 19 high on it. The cage is mostly open with nets of coarse
straps; the orange roof panel covers the rear of the cab and the light bar sits on the front roof tube.
The rear quarters have big arches over the exposed IRS and taper to a narrow tail with low red lamps;
the spare lies back on top of the tail on a tube carrier. Rivet rows run along the door and flare
edges. Livery is logo-free: orange with a dark band under the window line, a white pin and a dark
rocker; the 19 is on both cab B-panels and the skid. A-arms are dark tubes with gusset plates; wheel
centres are dark gunmetal (material `anod-blue` keeps its old name but is no longer blue).
