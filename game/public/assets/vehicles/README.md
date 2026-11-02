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

## KORPI 440 racer

The previous Kenney pickup has been replaced by original parametric geometry in
[`src/actors/prerunner`](../../../src/actors/prerunner/README.md). The vehicle has a
spaceframe, exposed V8, independent animated beadlock wheels, helical coilovers,
separate bypass shocks, a safety net and original fictional Finnish livery.
No external truck mesh or preparation step is needed. It uses the existing `truck`
physics/network kind and the shared-clock rare parking windows.
