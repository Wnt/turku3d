/** Reuse the retained project-owned volumetric hedge recipe; no image generation. */
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const { hedge } = await import(pathToFileURL(path.join(process.argv[2], 'game/scripts/nature/trees.mjs')));
process.stdout.write(JSON.stringify(Array.from({ length: 8 }, (_, i) => {
  const leaves = hedge(131 + i * 101, 1).leaves;
  return { positions: leaves.positions, normals: leaves.normals, uv: leaves.uvs,
    indices: leaves.indices, bounds: leaves.bounds() };
})));
