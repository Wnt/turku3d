# Walker bodies

`walkers.bin` holds three ambient walker bodies and their vertex animation texture (VAT), baked by
[`scripts/bake-walkers.mjs`](../../../scripts/bake-walkers.mjs) and decoded by
[`src/world/walkers/asset.ts`](../../../src/world/walkers/asset.ts).

| Variant | Outfit (CC0 MakeHuman via CharacterCreator) | Body morph |
|---|---|---|
| `hoodie` | hoodie, jeans, shoes, short02 hair (the player's outfit) | male 0.65 |
| `jacket` | shirt under jacket, jeans, boots, short04 hair | male 0.9 |
| `skirt` | shirt under jacket, skirt, boots, ponytail01 hair; bare legs are a separate tint region | female 0.75 |

Each outfit is retargeted into the Quaternius Universal Animation Library rig with the player's retarget
(`scripts/lib/character-outfit.mjs`), eyes, teeth and brows are dropped, and meshoptimizer simplifies the body to
at most 720 vertices (960–1,170 triangles). `Walk_Loop` (40 frames) and `Idle_Loop` (75 frames) are skinned on the
CPU at 30 fps into one RGBA16F texture, 720 × 345 texels, 1,987,200 bytes: xyz is the model-space position and w a
spherical Fibonacci normal index (2,048 directions). Seven regions (skin, top, bottom, shoes, hair, inner top,
legwear) are tinted per instance at runtime; the asset has no image textures. Exact sizes, source hashes and the
output hash are in [PROVENANCE.json](PROVENANCE.json).

Licences: CC0 1.0 for the MakeHuman assets (see [the player's notes](../character/README.md) and
[MAKEHUMAN-LICENSE-NOTES.md](../character/MAKEHUMAN-LICENSE-NOTES.md); the hoodie is the same CC0 source with the
project-original extension the player already uses) and for Quaternius UAL
([QUATERNIUS-LICENSE.txt](../character/QUATERNIUS-LICENSE.txt)). All other items are plain CC0 MakeHuman system assets.

## Rebuild

From `game/`, with no account, key or paid source (about two seconds after the download):

```sh
src=$TURKU3D_WORK/cache/walker-source; mkdir -p "$src"
for part in base_body clothing_hoodie clothing_jeans clothing_shoes clothing_shirt clothing_jacket clothing_boots \
  clothing_skirt hair_short02 hair_short04 hair_ponytail01; do
  curl --fail --location -o "$src/$part.glb" \
    "https://raw.githubusercontent.com/kblood/CharacterCreator/852e48f316b90a0937234886630e4fb4cdfd1f78/output/$part.glb"
done
git show 2d75e2c56c2db4a011f4197fe16f4367c494e0be:game/public/assets/character/mannequin.glb > "$src/ual.glb"
node scripts/bake-walkers.mjs "$src" "$src/ual.glb" public/assets/walkers
```

The bake is deterministic: the same sources reproduce `walkers.bin` byte for byte. It prints the report it writes to
PROVENANCE.json (vertex/triangle counts, clip frames, stride per walk loop, VAT size and hashes). Check the result with
`npx vitest run tests/unit/walkers.test.ts` (budget, frame counts, foot slip, shader programs).
