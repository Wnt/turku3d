# Autumn player

`player.glb` combines CC0 MakeHuman human, short hair, hoodie, jeans and shoes from
Kasper Olesen's CharacterCreator exports with Quaternius UAL Standard's rig and nine clips.
The human has a forest-green hoodie, blue jeans and brown shoes. The bind-pose height is
1.79 m. Textures use KTX2 (ETC1S colour, UASTC normals); geometry, skinning and animation
use `EXT_meshopt_compression`. Exact byte count, source hashes and output hash are in
[PROVENANCE.json](PROVENANCE.json).

Only exported CC0 data is used from CharacterCreator; none of its GPL implementation is
included. Its [original licence notes](MAKEHUMAN-LICENSE-NOTES.md) explicitly distinguish
CC0 generated assets from GPL code. [MAKEHUMAN-SOURCES.json](MAKEHUMAN-SOURCES.json)
records the upstream asset inventory. The actual selected files are in PROVENANCE.json.
Quaternius's original dedication is in [QUATERNIUS-LICENSE.txt](QUATERNIUS-LICENSE.txt).

## Retargeting

The offline preparation script maps the MakeHuman `game_engine` humanoid bones to UAL
bones, aligns their bind-pose limb directions without transferring exporter bone roll,
and rebinds every mesh with its original four skin weights. A 0.65 male shape is baked,
then unused morphs and fully clothed body triangles are removed. Identical vertices are
welded and unreferenced vertices are compacted; no triangle decimation is used.
The source torso spine joints are much closer together than the UAL joints. Torso and
clavicle vertices retain the source body's 1.035 height scale in the target bind pose;
otherwise the hoodie collar reaches the nose. The UAL animation joints remain unchanged.

This retargets the character into the existing animation skeleton. The UAL inverse bind
matrices, clip tracks, hip/leg names and motion timing remain authoritative. Runtime
locomotion blending, stair swing clearance, foot IK, physics and input remain unchanged.
This avoids maintaining a second runtime animation mixer or approximate clip conversion.
The ground pivot and +Z forward convention match the existing player.

Retained clips: `Idle_Loop`, `Walk_Loop`, `Walk_Formal_Loop`, `Jog_Fwd_Loop`, `Sprint_Loop`,
`Driving_Loop`, `Jump_Start`, `Jump_Loop`, `Jump_Land`. Stairs still use the formal walk
clip plus procedural tread placement, because the free UAL archive has no stair clips.

## Rebuild

From the repository root, in this sandbox (no paid source, account or API key):

```bash
source ../sandbox.env
npm ci --prefix game
export PATH="$HOME/turku3d/tools/bin:$PATH" # toktx 4.4.2
mkdir -p "$TURKU3D_WORK/cache/player-source"
for part in base_body clothing_hoodie clothing_jeans clothing_shoes hair_short02; do
  curl --fail --location "https://raw.githubusercontent.com/kblood/CharacterCreator/852e48f316b90a0937234886630e4fb4cdfd1f78/output/$part.glb" \
    --output "$TURKU3D_WORK/cache/player-source/$part.glb"
done
# Pinned, already trimmed UAL input; original archive provenance is in ../LICENSES.md.
git show 2d75e2c56c2db4a011f4197fe16f4367c494e0be:game/public/assets/character/mannequin.glb \
  > "$TURKU3D_WORK/cache/player-source/ual.glb"
node game/scripts/prepare-character.mjs "$TURKU3D_WORK/cache/player-source" \
  "$TURKU3D_WORK/cache/player-source/ual.glb" game/public/assets/character/player.glb
```

The script prints source hashes, retained clips, triangle count and final size. It caches
texture encodings only under the provided source directory. The GLB is committed, so
normal builds and tests do not download or encode anything.

## Verify

```bash
source ../sandbox.env
scripts/dev/gate --base main
cd game
npm run build
npx playwright test tests/e2e/character.spec.ts tests/e2e/gamepad.spec.ts
npx playwright test tests/e2e/character-look.spec.ts
```

The framing test checks both backends, the compressed asset, required locomotion clips,
and head/sole clearance in the default third-person view. The existing Xbox routes check
walk/run/sprint/jump, stair ascent/descent and ankle errors, camera input and car transitions.
Screenshots go to `$TURKU3D_WORK/reports/screenshots/*-player-framing.png` and `*-stair-ik.png`.
Set `PLAYER_CAPTURE=before` or `PLAYER_CAPTURE=after` for idle, walk and run screenshots
from both backends under `$TURKU3D_WORK/reports/player-look/<capture>/`.
