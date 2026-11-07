// Pure builder of the texture-free collision preview (world/collisionPreview.ts). It runs in a worker: the main
// thread only wraps the returned arrays, so a preview costs no main-thread decode, merge or bounds pass.
import { BoxGeometry, CapsuleGeometry, Color, CylinderGeometry, Quaternion, type BufferGeometry } from 'three';
import { readCol } from '../shared/col/colFormat';

type Decoder = NonNullable<Parameters<typeof readCol>[1]>['decoder'];
/** three's MeshoptSimplifier (ready). */
export interface Simplifier {
  simplify(index: Uint32Array, positions: Float32Array, stride: number, targetIndexCount: number, targetError: number, flags?: string[]): [Uint32Array, number];
}
/** A preview's surfaces are simplified to this geometric error (m, absolute) with cell borders locked: the collision
 *  cells are tessellated far finer than a flat-shaded, texture-free stand-in needs (a Turku tile: 160k -> 50k
 *  triangles), and previews were the largest beauty item on the low-end machines (0.5-1M triangles per frame). */
export const PREVIEW_ERROR_M = 0.05;

const palette = [0xaaa292, 0x55575a, 0xa7a399, 0xb9b5aa, 0xb9b5aa, 0x666768, 0x777470,
  0x8c877d, 0xaaa599, 0x958974, 0x748357, 0x82745a, 0xc3b48d, 0x8c8d87, 0x557e88,
  0x99958b, 0xa4a098, 0xa4a098, 0x99958b, 0xb7afa2, 0x777773, 0x777773, 0xc2b8a6,
  0x8b8076, 0x777775, 0x777773, 0x706852, 0x777773, 0x928d80, 0x748357, 0x777773, 0x9a8969];
const colors = palette.map(value => new Color(value));
/** Query helpers and invisible borders remain invisible. */
const hidden = (material: number) => material === 17 || material === 32;

export interface PreviewArrays {
  position: Float32Array; color: Float32Array; index: Uint32Array | Uint16Array;
  min: [number, number, number]; max: [number, number, number]; sphere: [number, number, number, number];
}

/** Every cell's surfaces in world-local metres (col origin applied) and its solid primitives, in cell order. */
export function collisionPreviewArrays(buffers: readonly ArrayBuffer[], decoder: Decoder, simplifier?: Simplifier): PreviewArrays {
  const parts: { position: Float32Array; color: Float32Array; index: ArrayLike<number>; offset: [number, number, number] }[] = [];
  for (const buffer of buffers) {
    const col = readCol(buffer, { decoder }), origin = col.header.origin;
    const color = new Float32Array(col.positions.length);
    let kept = 0;
    for (let t = 0; t < col.materials.length; t++) if (!hidden(col.materials[t])) kept++;
    const index = new Uint32Array(kept * 3);
    for (let t = 0, n = 0; t < col.materials.length; t++) {
      const material = col.materials[t];
      if (hidden(material)) continue;
      for (let k = 0; k < 3; k++) {
        const vertex = col.indices[3 * t + k]; index[n++] = vertex;
        (colors[material] ?? colors[0]).toArray(color, vertex * 3);
      }
    }
    const kept3 = simplifier && index.length > 3
      ? simplifier.simplify(index, col.positions instanceof Float32Array ? col.positions : new Float32Array(col.positions), 3, 0, PREVIEW_ERROR_M, ['ErrorAbsolute', 'LockBorder'])[0]
      : index;
    parts.push({ position: col.positions, color, index: kept3, offset: origin });
    for (const p of col.primitives) {
      if (hidden(p.material)) continue;
      const [h, r, z] = p.params;
      const shape: BufferGeometry = p.kind === 1 ? new BoxGeometry(h * 2, r * 2, z * 2)
        : p.kind === 2 ? new CapsuleGeometry(r, h * 2, 2, 6)
        : new CylinderGeometry(r, r, h * 2, 6);
      shape.applyQuaternion(new Quaternion(...p.rotation));
      shape.translate(origin[0] + p.center[0], origin[1] + p.center[1], origin[2] + p.center[2]);
      const position = shape.attributes.position.array as Float32Array, tint = colors[p.material] ?? colors[0];
      const shapeColor = new Float32Array(position.length);
      for (let i = 0; i < shapeColor.length; i += 3) tint.toArray(shapeColor, i);
      parts.push({ position, color: shapeColor, index: shape.index!.array, offset: [0, 0, 0] });
      shape.dispose();
    }
  }
  const vertices = parts.reduce((sum, part) => sum + part.position.length / 3, 0);
  const indices = parts.reduce((sum, part) => sum + part.index.length, 0);
  const position = new Float32Array(vertices * 3), color = new Float32Array(vertices * 3);
  const index = vertices > 65535 ? new Uint32Array(indices) : new Uint16Array(indices);
  const min: [number, number, number] = [Infinity, Infinity, Infinity], max: [number, number, number] = [-Infinity, -Infinity, -Infinity];
  let base = 0, at = 0;
  for (const part of parts) {
    const [ox, oy, oz] = part.offset;
    for (let i = 0; i < part.position.length; i += 3) {
      const o = base * 3 + i;
      position[o] = part.position[i] + ox; position[o + 1] = part.position[i + 1] + oy; position[o + 2] = part.position[i + 2] + oz;
      // Bounds of the stored float32 values, as three computes them.
      const x = position[o], y = position[o + 1], z = position[o + 2];
      if (x < min[0]) min[0] = x; if (y < min[1]) min[1] = y; if (z < min[2]) min[2] = z;
      if (x > max[0]) max[0] = x; if (y > max[1]) max[1] = y; if (z > max[2]) max[2] = z;
    }
    color.set(part.color, base * 3);
    for (let i = 0; i < part.index.length; i++) index[at++] = part.index[i] + base;
    base += part.position.length / 3;
  }
  // three's computeBoundingSphere: the box centre and the farthest vertex from it.
  const cx = (min[0] + max[0]) / 2, cy = (min[1] + max[1]) / 2, cz = (min[2] + max[2]) / 2;
  let radius2 = 0;
  for (let i = 0; i < position.length; i += 3) {
    const dx = position[i] - cx, dy = position[i + 1] - cy, dz = position[i + 2] - cz;
    radius2 = Math.max(radius2, dx * dx + dy * dy + dz * dz);
  }
  return { position, color, index, min, max, sphere: [cx, cy, cz, Math.sqrt(radius2)] };
}
