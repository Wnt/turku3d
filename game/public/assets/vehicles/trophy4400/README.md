# trophy4400: #19-style 4400 car (raw Blender export)

Our own model of a four-wheel-steer, fully independent (IFS + IRS) 4400-class car in the style of the
#19 car, modelled from scratch in Blender 5.2 through the blenderwright 3.1.0 MCP tools (parameterised
tool calls only; no Blender Python). No downloaded mesh, sponsor logo, brand mark or photograph is
included. The textures are the generated "19" plate (`livery/plate19.svg`, our own stroke numerals,
rasterised with rsvg-convert) and four tileable worn-paint / dusty-panel maps generated with numpy
(`~/turku3d/loops/trophy4400/model/gen/tex_gen.py`). Copyright 2026 Jonni Madekivi; MIT, as the repository.

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
| tyre OD x width | 1.073 x 0.420 m (lugs), mud-terrain: 20 pitches of staggered long/short shoulder lugs plus deep sidewall blocks; beadlock with 24 bolts |
| L0 bbox (excl. whip) | x -2.48 .. 2.12 (rear tube bumper .. bumper hoop / front tyre), width 2.60 over tyres, body 1.99 over the front flares / 1.55 at the cab, rocker 0.62, fender top 1.333, roof panel 1.90-1.93, light bar 1.97 |
| triangles L0 / L1 / L2 | 147,764 / 26,923 / 3,729 (body alone 29,246 / 4,757 / 1,133) |
| raw primitives (all LODs) | 222 (before the per-material merge) |
| sha256 (raw) | c88f00f60afe5b6e1bdeda3dacb2748b9a26ed7c3c351cf6c5a2e83e36738f54 |

Node contract (SPEC 2.2): `trophy4400` root; `body` (`body_L0/L1/L2`); per corner `uca-*`, `lca-*`,
`upright-*` > `wheel-*` (tyre/rim `_L0/_L1/_L2`), `tierod-*`, `coil-*-body/-shaft/-spring`,
`bypass-*-body/-shaft`, `cv-*-inner/-shaft/-outer`; `rack-front-slider`, `rack-rear-slider`,
`steer-wheel`, `spare`, `antenna`, lamp/seat/exit empties and 64 `hp-*` hardpoints under `hardpoints`.
Rig nodes are empties at the pivots; axial nodes point Blender +Z (glTF +Y) from top/inner to
bottom/outer. Lamp lenses use `led-optics` / `tail-lens`.

Round 6 body (2026-11-05): deep curved front flares (an arch shell around each hub, a flat top behind
the crown, a side skirt with the wheel opening, a front face that wraps down in front of the tyre into the
nose side and a rear return into the door), a raised crowned hood with a vent and a dark lower nose,
compact black-bodied round LED pods and a visible winch inside the tube bumper with a red soft shackle,
and a smaller sloped skid plate with the 19 that leaves the dark A-arms and coilovers visible. The cab
has a full orange roof with the light bar on its leading edge, wide orange B-panels and a header strip
that frame an inset net window. Solid orange rear quarters rise to the cab, with rear flares and a top
flange, and taper to the tail; the spare stands nearly upright behind the cab on a tube carrier. The
livery is logo-free: a large dark door band with generated white text-like glyphs (no real word or
mark), a smaller dark text-like line, blank white sponsor-area blocks, a white pin and a dark rocker.
Paint and dark panels carry world-scaled (1024 px = 2 m) tileable base colour + roughness maps with
mottling, dust and fine scratches; arms and cage are dark powder coat; rubber is dusty dark brown-grey.
No normal or AO maps yet. LOD1 is about 1.9k over its 25k budget (linkage and wheels).

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
