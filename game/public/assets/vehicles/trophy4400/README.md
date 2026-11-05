# trophy4400: #19-style 4400 car

Our own model of a four-wheel-steer, fully independent (IFS + IRS) 4400-class car in the style of the
#19 car. **MCP rebuild (2026-11-05):** modelled from scratch in Blender 5.2 with blenderwright MCP tool calls made
one at a time (no Blender Python, no generator scripts or call batches), shaped against camera-matched reference
stills (image planes parented to the matched cameras). Nothing was appended or joined from earlier rounds. No
downloaded mesh, sponsor logo, brand mark or photograph is included; the only text is the "19" (Blender's default
font, converted to mesh). Copyright 2026 Jonni Madekivi; MIT, as the repository.

Source (local, not in git): `~/turku3d/loops/trophy4400/model/blend/scratch-r3.blend` (parts) and
`scratch-r3-export.blend` (joined LODs, as exported). The per-round tool log is
`~/turku3d/loops/trophy4400/model/MCP-LOG.md`, and the overlay/edge/model captures are in
`~/turku3d/loops/trophy4400/model/previews/mcp-round-N/`.

`trophy4400.glb` is the **prepared** runtime asset (Y-up, +X forward, left = -Z, ground origin, x = 0 at
mid-wheelbase), built from the raw Blender export by

```sh
node game/scripts/prepare-trophy4400.mjs   # ~/turku3d/data/assets-src/trophy4400/trophy4400_raw.glb -> this folder
```

It validates the node contract, makes every material single-sided except `net-black`, encodes any textures as
KTX2 ETC1S (cached by content hash in `assets-src/trophy4400/build/ktx2`), meshopt-compresses the geometry
(positions 14 bit) and writes `hardpoints.json` (SPEC 2.3, plus the spring seats and coil lengths). 0.57 MB;
the MCP rebuild uses plain PBR materials and no textures. At load (`src/actors/trophy4400/asset.ts`) every mesh of a LOD is merged per
material into rigid-skinned meshes whose bones are the rig nodes, so a truck costs 29 / 29 / 25 draws at L0 / L1 / L2
(was 124 / 122 / 45 as separate nodes); the linkage solver (`linkage.ts`) and rig (`rig.ts`) pose the bones from
each wheel's synced hub height and steer.

| quantity | value |
|---|---|
| wheelbase / track (wheel-* origins) | 3.160 / 2.180 m (physics layout) |
| tyre OD x width | 1.072 x 0.33 m: a lathed squared-shoulder carcass with 36 x (two staggered tread lugs + two shoulder lugs); dark wheel with a wide orange beadlock ring (0.34-0.52 m dia), 24 bolts and a gold hub |
| frame | round 3: a tube chassis and cage laid over the undistorted CAD frames (main hoop x -0.70, roof x -0.70..0.50 at 1.84, A-pillars to the cowl node x 0.92, front clip carrying the LCA pivots, rear section to the shock towers at the coil tops and the tail), black powder coat (`cage-powder`) |
| body | no fenders: a lattice-shaped hull widest at the cockpit (+-0.88) narrowing to the nose (+-0.47) and tail (+-0.60) with tumblehome; front cutouts r 0.66 and rear r 0.56 cut only outboard of the narrow nose/tail, leaving a thin hood strip; open cockpit and rear deck; a dark lower band and door band with abstract white blocks; round LEDs, grille, winch, tube bumper loop and a V skid with the 19; three oval tail lamps (red, amber, red) with bezels, a braced raw bumper, white 19 plates on the corner posts, an alu deck and fan shroud with twin fans, an exhaust, a red tow loop and the spare lying on the rear rails |
| cab | thin orange roof x -0.70..0.50 at 1.84-1.88 m (measured on the side still through the refit camera), leaning B-pillar panels, orange A-pillar strips, window nets (`net-black`), light bar at the roof front edge, round mirrors on stalks, pod lights, two seats |
| suspension | unchanged from the MCP rebuild: boxed square-section A-arms with gusset plates; slotted top plate on the uppers; rear billet portal cases (CV input 0.12 m above the hub); coilovers with wire springs, bypass bodies, CV shafts, tie rods and rack sliders on the rig nodes |
| triangles L0 / L1 / L2 | 83,887 / 22,927 / 2,224 (0.57 MB) |
| sha256 (raw) | 97cc47fd3199534d31458b1bacecef1308d29fa27309f6c9dd1403ae9f005eac |

Node contract (SPEC 2.2): `trophy4400` root; `body` (`body_L0/L1/L2`); per corner `uca-*`, `lca-*`,
`upright-*` > `wheel-*` (tyre/rim `_L0/_L1/_L2`), `tierod-*`, `coil-*-body/-shaft/-spring`,
`bypass-*-body/-shaft`, `cv-*-inner/-shaft/-outer`; `rack-front-slider`, `rack-rear-slider`,
`steer-wheel`, `spare`, `antenna`, lamp/seat/exit empties and 64 `hp-*` hardpoints under `hardpoints`.
Rig nodes are empties at the pivots; axial nodes point Blender +Z (glTF +Y) from top/inner to
bottom/outer. Lamp lenses use `led-optics` / `tail-lens`.

Round 12 (2026-11-05, a new body from the verified dimension sheet, `~/turku3d/loops/trophy4400/model/gen8/`, previews
`previews/round-10..12/`). The hardpoints and the node contract are unchanged, so `hardpoints.json` is byte-identical.
- Body: narrow (doors y +-0.79) on the fixed 2.18 m track, so the tyres stand well outboard on exposed arms. The
  front fenders are crisp flat slabs whose tops slope down to a lip at y +-1.02, with tall arch openings (1.30 m).
  The nose is short (sheet front x 1.78) with a dark slotted fascia, a slot vent along the hood lip and hood rivets.
- Cab: a flat-sided orange cab box (x -0.78 .. 0.60) with a sparse net window, leaning in 0.14 m per side to the
  flat orange roof at 1.90 m (tumblehome from the CAD frames). There is a full-width light bar on the roof front edge.
- Rear: the quarter tapers and rises to a raised tail with orange corner wraps. The rear is open above a full-width
  tube bumper with an LED tail bar. The spare is tilted 42 deg on a tube rack (centre x -2.0, z 1.38).
- Front end: a thin tube bumper hoop (front x 2.17, +-0.68) with the winch, a big fairlead and a soft shackle. Two
  recessed round 7-in LEDs sit in dark openings in the nose corners. Below them is a tall dark V skid with the 19.
- Livery: a clean dark door band (z 1.04-1.32, angled leading edge) with three abstract white stripes. There is no
  text-like graphic. The 19 plates are on the skid and on the cab-rear pillars.
- Paint: deep satin orange (#B23004, roughness 0.26, full clearcoat).

Round 9 (2026-11-05, on the round-7 body, `~/turku3d/loops/trophy4400/model/gen9/`): the round-8 tyre and an
orange-ring beadlock on a dark centre; the spare lies across the rear deck tilted 38 deg (centre x -2.0, z 1.30) under a
strap; an orange roof skin over the cockpit (x +0.35 .. -0.80) under the light bar; the door band carries abstract
slanted stripes and a chevron only (the letter-like glyph rows are painted over); gloss deep orange-red paint
(#C94008 albedo, roughness ~0.25, full clearcoat, no specular/IOR extensions) with chips instead of line scratches;
dark powder-coated arms (metallic 0.3, roughness 0.52).

Round 7 body (2026-11-05): short nose that stops at the front of the front tyres: a raised hood
(1.39-1.41 m) that drops steeply into a tall vented face, with a tall upright tube bumper hoop (top 1.44 m)
in front carrying two round LED pods with dark bezels and smoked lenses, the winch between the uprights and
a red soft shackle. The front flares are thick arch rings with a rounded crown (inner r 0.72, outer 0.84,
squeezed to 0.88 along X so they wrap down beside the lights into the nose sides, outer edge drooped 7 cm),
reaching out to y +-1.17 over ~70 % of the tyre width with a high arch opening (1.25 m). The forward cage
is open: steep A-pillars to the cowl, a central windscreen bar and a long light bar across the full front
hoop (1.64 m). A tall orange B-pillar box with a net window and the #19 carries a short roof panel over the
rear of the cockpit only. The body kicks up behind the cab (rear quarters 1.45 -> 1.72 m) to an engine cover
and a tall angled rear panel (top 2.03 m), with the spare upright on rails behind it and rear flare rings
over the rear tyres. The large dark skid is a trapezoid (0.80 -> 0.44 m wide) from the bumper down to the
lower arms, with the 19. Door rocker raised to 0.72 m for more air under the body. Livery is logo-free and
built per side so it reads front-to-back on both sides: a dark band filling the upper door with bold white
text-like glyphs, a second large dark-on-orange glyph block below it, a pin, a dark rocker. New materials:
`paint-orange-dust` (flares, lower door, returns) with a heavier dust/chip map, `skid-dark` with a scuffed
map whose blue channel drives metallic where the coating is scraped off, raw-steel arms, black rubber.
Still no normal or AO maps. LOD1 is about 2.5k over its 25k budget (linkage and wheels).

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
