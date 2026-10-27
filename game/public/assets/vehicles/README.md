# Vehicle assets

## Two-wheeler

| | |
|---|---|
| File | `scooter.glb` (188,220 bytes, SHA-256 `fc08098681c5fc727b36d4ea2de69fc76042f3638d62d6eea7ff3e5731b3c50c`) |
| Model | "Scooter" by **MiniPoly** |
| Source | https://poly.pizza/m/3vLV1QtLFP (GLB `https://static.poly.pizza/afe6bead-8f20-4702-b27b-6814e17f9b6d.glb`), downloaded 2026-10-27 |
| Source file | 198,188 bytes, SHA-256 `567b0023a08729bfa00003b8da48c3964bbb071eb3241818f38096e7d9964813`, 2.1 k triangles, one mesh, 512 px palette texture |
| Licence | [Creative Commons Attribution 3.0](https://creativecommons.org/licenses/by/3.0/) (CC BY 3.0), as stated on the source page |
| Attribution | "Scooter" by MiniPoly (https://poly.pizza/m/3vLV1QtLFP), CC BY 3.0. Modified for Turku3D. |

### Changes

`game/scripts/prepare-scooter.mjs` bakes the model into the scooter's physics frame and strips one texture:

- turned so that +X is forward and +Y up, scaled by 0.7283 so the measured tyre contacts sit on the physics wheel
  positions (`SCOOTER.wheels[0..1]` in `src/physics/constants.ts`, 1.25 m wheelbase), tyre contact at y = 0;
- the node hierarchy is flattened into the mesh;
- the metal/roughness texture is removed (metallic 0, roughness 0.75), so it shades like the matte Kenney
  palette cars; base colour palette and lamp emission are kept.

The red pizza case on the rear rack is drawn at runtime (`src/actors/scooter.ts`) and is not part of this file.

Rebuild (run in `game/`; the source file is not kept in the repository):

```bash
mkdir -p "$TURKU3D_WORK/cache/scooter" && curl -sSLo "$TURKU3D_WORK/cache/scooter/source.glb" \
  https://static.poly.pizza/afe6bead-8f20-4702-b27b-6814e17f9b6d.glb
npm run prepare-scooter -- "$TURKU3D_WORK/cache/scooter/source.glb" public/assets/vehicles/scooter.glb
```

### Selection (2026-10-27)

Searched in the brief's order. Kenney (Car Kit 3.1, Toy Car Kit, Racing Kit, City kits; tag "vehicle"): no
two-wheeler. Quaternius: no scooter; the CC0 *LowPoly Public Transport* pack has a bicycle but ships only
FBX/OBJ/Blend. poly.pizza: every scooter/moped is CC BY 3.0 (MiniPoly, Poly by Google, s1pierro, Jasmine
Roberts, Julien Savoie, jeremy); MiniPoly's chunky flat-palette scooter is the closest match to the Kenney cars.
OpenGameArt (CC0): only car packs. KayKit: no vehicle pack with a two-wheeler. styloo's *Simple Scooter*
(itch.io, CC0) was rejected: paid download and 2048 px PBR textures in a different style.

## Trophy truck

| | |
|---|---|
| File | `truck.glb` (79,188 bytes, SHA-256 `162f45d3d2d28eec5a690ce734d7181382b3b70abde5b90699c4c033cddcbb07`) |
| Model | `truck.glb` (a pickup) from Kenney's **Car Kit 3.1** |
| Source | https://kenney.nl/assets/car-kit, archive `kenney_car-kit.zip` (SHA-256 `fac7dacac5c7874348cf19729af3ef205f3d366493edaf0a827d93f4fdf3d0c4`, the same archive as `../car/sedan.glb`), `Models/GLB format/truck.glb` (176,360 bytes, SHA-256 `5e1bc66c343501dd7d5734b57e672dd949bf95d7abdce680c6508cef99d374f8`, 754-triangle body, four 332-triangle wheels) with `Textures/colormap.png` (12,371 bytes, 512 px palette), downloaded 2026-10-27 |
| Licence | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) ([Kenney's licence file](../car/KENNEY-LICENSE.txt)); no attribution required |

### Changes

`game/scripts/prepare-truck.mjs` (offline, from the archive):

- embeds the palette texture; keeps the body and one wheel (`wheel`, from `wheel-front-left`);
- removes the 10 window-glass triangles (palette cell column 1, rows 13–14) so the cab is open like a trophy
  truck's netted window frames and the seated driver shows;
- repaints the body's 158 green triangles in the palette's orange column (UV shift by four 32 px cells).

`src/actors/truck.ts` (at runtime, all flat-shaded): stretches the cab and bed to the physics wheelbase (2.9 m)
while the cross-section and overhangs scale by 1.35, lifts the body 25 cm, builds four 0.92 m tyres (0.42 m wide)
from the wheel, and adds in code a roll cage over the bed, a roof light bar with four lenses, a spare tyre in the
bed, a front bar and yellow coilovers whose length follows the suspension. No BeamNG or other non-redistributable
content is used.

Rebuild (run in `game/`; the archive is not kept in the repository):

```bash
mkdir -p "$TURKU3D_WORK/cache/truck" && curl -sSLo "$TURKU3D_WORK/cache/truck/car-kit.zip" \
  https://kenney.nl/media/pages/assets/car-kit/1a312ec241-1775131960/kenney_car-kit.zip
unzip -q -o "$TURKU3D_WORK/cache/truck/car-kit.zip" -d "$TURKU3D_WORK/cache/truck/car-kit"
npm run prepare-truck -- "$TURKU3D_WORK/cache/truck/car-kit/Models/GLB format/truck.glb" public/assets/vehicles/truck.glb
```

### Selection (2026-10-27)

Searched for a CC0 or CC BY low-poly trophy truck, baja prerunner or desert buggy in glTF, in the brief's order.
BeamNG mods were excluded up front (not redistributable). Kenney Car Kit 3.1: no buggy or trophy truck, but a
pickup (`truck.glb`) in exactly the style of the game's sedan. Quaternius (poly.pizza, CC0): *Pickup Truck* (a
realistic 1970s pickup, 6.4 k triangles, a different style), *Rover* (six-wheeled), *Pickup Thunder* (an icon);
no trophy truck. poly.pizza: Nick Slough's two *Buggy* models are CC BY 3.0 dune buggies (614/367 KB, another
style, one on a diorama base); no trophy or monster truck under CC0/CC BY. OpenGameArt: rgsdev's CC0 vehicle pack has
a monster truck, not a prerunner, without glTF; RayB2's CC0 vehicles ship only as `.blend`. KayKit: no off-road
truck. Every trophy truck found elsewhere is paid (CGTrader, Sketchfab store). So the brief's fallback applies:
the Kenney pickup, lifted, on big tyres and with a cage, built in code.
