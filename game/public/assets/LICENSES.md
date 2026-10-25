# Third-party assets in game/public/assets

| File | Source | Licence | Notes |
|---|---|---|---|
| `character/mannequin.glb` | Quaternius, *Universal Animation Library [Standard]* (UAL1), `Godot/AnimationLibrary_Godot_Standard.glb` from https://opengameart.org/sites/default/files/universal_animation_librarystandard.zip (page: https://opengameart.org/content/universal-animation-library). Zip sha256 `18ff1a7215f4852b320203e8aaf02a1578b5c8eef9027fbaedfcedc7b85a3ac2`, downloaded 2026-10-25 | **CC0 1.0** (see `character/QUATERNIUS-LICENSE.txt`) | Trimmed by `node scripts/prepare-character.mjs <in.glb> public/assets/character/mannequin.glb` to the clips Idle_Loop, Walk_Loop, Walk_Formal_Loop, Jog_Fwd_Loop, Sprint_Loop, Driving_Loop, Jump_Start, Jump_Loop, Jump_Land (resampled, pruned). 1.83 m tall, feet at y = 0, faces +Z |
| `car/sedan.glb` | Kenney, *Car Kit 3.1*, `Models/GLB format/sedan.glb` + its `Textures/colormap.png` from [Car Kit](https://kenney.nl/assets/car-kit) | **CC0 1.0** ([original license](car/KENNEY-LICENSE.txt)) | Embedded palette with `scripts/prepare-car.mjs`; body/wheel transforms adapted at runtime to the physics dimensions. Separate authored wheel meshes follow suspension, steering and rotation. |

Car source archive: `https://kenney.nl/media/pages/assets/car-kit/1a312ec241-1775131960/kenney_car-kit.zip`, downloaded 2026-10-25,
SHA-256 `fac7dacac5c7874348cf19729af3ef205f3d366493edaf0a827d93f4fdf3d0c4`.
Embedded sedan SHA-256 `f867bdd69399bbcf68cdfbd02d5c98c9b95ed965875029656c359baff90b741f`.
Rebuild from the extracted archive (run in `game/`):

```bash
node scripts/prepare-car.mjs '/path/to/Car Kit/Models/GLB format/sedan.glb' public/assets/car/sedan.glb
```

`../basis/basis_transcoder.{js,wasm}` are copied from `three/examples/jsm/libs/basis` (three 0.186.1, Apache-2.0, Binomial LLC).

`Walk_Formal_Loop` was added from the same pinned archive for the slower stair gait (2026-10-25).
The archive's 46 named clips were inspected: none is a dedicated stair up/down clip. Stair states therefore use
procedural swing clearance and two-bone foot IK against query treads. Publisher CC0 declaration:
https://quaternius.com/packs/universalanimationlibrary.html . No paid animation pack is used.
