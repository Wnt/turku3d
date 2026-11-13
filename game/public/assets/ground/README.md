# Ground surfaces

The photographic ground library the ground material families draw from
(`game/src/world/location/families/ground.ts`): `asphalt_worn`, `grass`, `gravel`, `granite_curb` and
`cobblestone`, each one repeat of 2 m as `albedo.jpg` (1024 px, sRGB), `normal.jpg` (512 px, tangent space) and
`orm.jpg` (256 px: occlusion, roughness, metalness).

They are the project's own ground library of the first generation, catalogued in `textures/library/ground.json`
(keys, repeat size and the description each image was generated from) with provenance and per-file checks in
`docs/reports/ground-textures.json`: images generated for this project with OpenAI image generation, made seamless,
with normals derived from luminance. Not third-party; not a CC0 source asset.

The JPEG files here are re-encoded from that library's extracted layers
(`$TURKU3D_DATA/coarse/.surface-library-*/draft_<key>/`): albedo at quality 84, normal and ORM at 90.
