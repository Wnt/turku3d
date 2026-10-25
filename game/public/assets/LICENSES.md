# Third-party assets in game/public/assets

| File | Source | Licence | Notes |
|---|---|---|---|
| `character/player.glb` | MakeHuman assets exported by [Kasper Olesen / CharacterCreator](https://github.com/kblood/CharacterCreator/tree/852e48f316b90a0937234886630e4fb4cdfd1f78/output), plus Quaternius *Universal Animation Library [Standard]* | **CC0 1.0** | Textured autumn player; offline skin retarget to UAL, KTX2 and meshopt. [Licence, provenance and exact rebuild](character/README.md). |
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

UAL source archive: https://opengameart.org/sites/default/files/universal_animation_librarystandard.zip
(page: https://opengameart.org/content/universal-animation-library), downloaded 2026-10-25.
Archive SHA-256: `18ff1a7215f4852b320203e8aaf02a1578b5c8eef9027fbaedfcedc7b85a3ac2`.
The pinned trimmed input is recoverable from commit `2d75e2c56c2db4a011f4197fe16f4367c494e0be`;
the old mannequin is no longer shipped.
