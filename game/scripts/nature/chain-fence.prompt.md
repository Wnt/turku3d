You are adding one module to a kit of Finnish city fences in the running Blender 5.2 instance. Use ONLY the Blender MCP tools you have (mcp__blender__*): build geometry with `execute_blender_code`, inspect with `get_scene_info` and `look`. Do not use asset libraries or generators.

First open `/Users/wnt/turku3d/data/assets-src/nature/blender/fence-kit.blend` (it holds the existing kit: picket_fence, board_fence, metal_railing, park_fence, granite_wall, each _lod0 and _lod1). Keep those objects unchanged.

## Conventions (same as the kit)
- Metres, Blender Z up. The module runs along X from x = -1.0 to x = +1.0 (2.0 m), centred in Y, standing on z = 0, with exactly ONE post centred at x = -1.0. Thickness along Y.
- UVs: for the mesh panel, U runs along X and V along Z, 1 UV unit = 1 metre, U from 0 at x = -0.97 to 1.94 at x = +0.97, V from 0 at z = 0.04 to 1.56 at z = 1.60. For posts and rails any box/cylinder projection at 1 unit = 1 m with U along the long axis.
- Smooth tubes (mark their circumferential edges smooth), crisp boxes.
- Materials: reuse `painted_metal` for nothing here; create `galvanised` (base colour 0.55,0.57,0.56, metallic 0.8, roughness 0.45) for posts and rails, and `chain_mesh` (base colour 0.6,0.62,0.6, metallic 0.7, roughness 0.5) for the wire panel. No textures.

## Objects
1. `chain_fence_lod0`, a galvanised chain-link fence 1.6 m tall:
   - post: round tube, diameter 0.06, 8 sides, z 0 to 1.62, at x = -1.0, with a flat round cap;
   - top rail: round tube, diameter 0.042, 8 sides, axis along X at z = 1.58, x -1.0 to +1.0;
   - bottom tension wire: round tube, diameter 0.008, 4 sides, axis along X at z = 0.05, x -1.0 to +1.0;
   - wire panel: ONE quad in the plane y = 0, x from -0.97 to +0.97, z from 0.04 to 1.60, material `chain_mesh`, UVs as above (the diamond pattern comes from an alpha texture in the game).
   Budget 160 triangles.
2. `chain_fence_lod1`: post as a 4-sided box, top rail as a 4-sided box, no bottom wire, the same panel quad. Budget 30 triangles.

## Output
- Apply modifiers, recalculate normals outward (the panel's normal may point either way; it is drawn double-sided).
- Save the scene back to `/Users/wnt/turku3d/data/assets-src/nature/blender/fence-kit.blend`.
- Export ALL kit objects (the 10 existing plus the 2 new ones) to `/Users/wnt/turku3d/data/assets-src/nature/blender/fence-kit.glb` with the glTF exporter: GLB, +Y up, apply modifiers, UVs and normals, materials without images, no cameras, lights or animations.
- Rewrite `/Users/wnt/turku3d/data/assets-src/nature/blender/fence-kit.json` (from Blender Python) for all 12 objects: name, material(s), triangle count after triangulation, vertex count, bounding box in Blender coordinates.
- Use `look` to check the new module next to two copies (delete copies before export): one post at x = -1.0, rail and wire run the full module, the panel reaches the post and stops short of the next post.
- Final message: the JSON rows of the two new objects and 2-3 lines on what you verified.
