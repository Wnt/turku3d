# Swept crop source meshes

These CC0 GLBs derive from CharacterCreator hair_short02 at pinned revision 852e48f316b90a0937234886630e4fb4cdfd1f78. They were edited only through Blenderwright MCP on port9877.

A 3×3×4 Catmull–Rom lattice raises the front-lower row by 35 mm and moves it back 18 mm; the upper-front row moves 12 mm left, 6 mm back and 18 mm up; the top-right column moves 12 mm left and 8 mm down. Smooth shading is enabled. The exports evaluate the male morph at 0.70 and 0.55 respectively, keeping the source armature and baking the lattice. Their intended recipes are tee-denim-crop and hoodie-denim.

The local Blender file is `~/turku3d/data/assets-src/actors/blender-hair/swept-crop.blend`; viewport previews and a local licensed portrait reference live outside the repository. The reference informs the hairline and volume only. No photograph pixels enter these models or textures. Source and generated content hashes are recorded in the parent PROVENANCE.json.

The bake selects topology and UVs from the original CC0 hair, then transfers the edited positions and normals by source-UV correspondence. It keeps the original atlas pixels, near/far indices and vertex ordering.
