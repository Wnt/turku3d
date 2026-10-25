# Third-party assets in game/public/assets

| File | Source | Licence | Notes |
|---|---|---|---|
| `character/mannequin.glb` | Quaternius, *Universal Animation Library [Standard]* (UAL1), `Godot/AnimationLibrary_Godot_Standard.glb` from https://opengameart.org/sites/default/files/universal_animation_librarystandard.zip (page: https://opengameart.org/content/universal-animation-library). Zip sha256 `18ff1a7215f4852b320203e8aaf02a1578b5c8eef9027fbaedfcedc7b85a3ac2`, downloaded 2026-10-25 | **CC0 1.0** (see `character/QUATERNIUS-LICENSE.txt`) | Trimmed by `node scripts/prepare-character.mjs <in.glb> public/assets/character/mannequin.glb` to the clips Idle_Loop, Walk_Loop, Jog_Fwd_Loop, Sprint_Loop, Driving_Loop, Jump_Start, Jump_Loop, Jump_Land (resampled, pruned). 1.83 m tall, feet at y = 0, faces +Z |

`../basis/basis_transcoder.{js,wasm}` are copied from `three/examples/jsm/libs/basis` (three 0.186.1, Apache-2.0, Binomial LLC).
