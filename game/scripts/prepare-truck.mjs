// Turn Kenney's CC0 Car Kit pickup (`truck.glb`) into the trophy truck's base: embed the external palette texture,
// open the cab (drop the window panes so the seated driver shows, as in a trophy truck's netted window openings),
// repaint the green body in the palette's orange column, and keep only the body and one wheel (the runtime builds
// four big tyres from it, lifts the body and adds the cage: src/actors/truck.ts).
// Usage: node scripts/prepare-truck.mjs '/path/to/Car Kit/Models/GLB format/truck.glb' public/assets/vehicles/truck.glb
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { prune } from '@gltf-transform/functions';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';

const [source, destination] = process.argv.slice(2);
if (!source || !destination) throw new Error('Usage: prepare-truck.mjs <Car Kit truck.glb> <output.glb>');

// Kenney's 512 px colormap is a grid of 32 px palette cells: column 1, rows 13-14 are the pale blue window glass;
// column 7, rows 4-7 the body green gradient, and column 11 the same gradient in orange.
const CELL = 32 / 512;
const cell = (u, v) => [Math.floor(u / CELL), Math.floor(v / CELL)];
const isGlass = ([c, r]) => c === 1 && (r === 13 || r === 14);
const isPaint = ([c, r]) => c === 7 && r >= 4 && r <= 7;
const ORANGE_SHIFT = 4 * CELL;

const io = new NodeIO().registerExtensions(ALL_EXTENSIONS);
const doc = await io.read(source);
const root = doc.getRoot();
const scene = root.getDefaultScene() ?? root.listScenes()[0];
const body = root.listNodes().find(n => n.getName() === 'body');
const wheel = root.listNodes().find(n => n.getName() === 'wheel-front-left');
if (!body?.getMesh() || !wheel?.getMesh()) throw new Error('expected the Car Kit truck with body and wheel-front-left');

let glass = 0, paint = 0;
for (const prim of body.getMesh().listPrimitives()) {
  const uv = prim.getAttribute('TEXCOORD_0'), indices = prim.getIndices();
  const kept = [];
  const repaint = new Set(), other = new Set();
  for (let t = 0; t < indices.getCount(); t += 3) {
    const tri = [indices.getScalar(t), indices.getScalar(t + 1), indices.getScalar(t + 2)];
    const centre = tri.reduce((sum, i) => { const [u, v] = uv.getElement(i, []); return [sum[0] + u / 3, sum[1] + v / 3]; }, [0, 0]);
    const where = cell(centre[0], centre[1]);
    if (isGlass(where)) { glass++; continue; }
    if (isPaint(where)) { paint++; tri.forEach(i => repaint.add(i)); } else tri.forEach(i => other.add(i));
    kept.push(...tri);
  }
  // Painted vertices must carry only paint UVs, or the repaint would bleed into a neighbouring colour.
  for (const i of repaint) if (other.has(i)) throw new Error(`vertex ${i} is shared by painted and unpainted faces`);
  for (const i of repaint) { const [u, v] = uv.getElement(i, []); uv.setElement(i, [u + ORANGE_SHIFT, v]); }
  indices.setArray(new Uint32Array(kept));
}
for (const node of root.listNodes()) {
  if (node === body || node === wheel) continue;
  for (const parent of [scene, ...root.listNodes()]) parent.removeChild?.(node);
  node.dispose();
}
wheel.setName('wheel');
await doc.transform(prune());
await io.write(destination, doc);
const bytes = await readFile(destination);
console.log(`${destination}: ${glass} glass and ${paint} paint triangles, ${bytes.length} bytes, sha256 ${createHash('sha256').update(bytes).digest('hex')}`);
