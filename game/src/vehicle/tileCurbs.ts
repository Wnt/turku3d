// Shared tile-to-wheel-query adapter for the game and the Node acceptance validator.
import type * as Rapier from '@dimforge/rapier3d';
type RapierApi = Pick<typeof Rapier, 'TriMesh' | 'Ray'>;
import { TriLocator } from '../physics/triLocator.ts';
import type { CurbQueries } from './curbQueries.ts';
import { isCurbMaterial } from './curbStrike.ts';

export function addCurbMesh(R: RapierApi, queries: CurbQueries, collider: Rapier.Collider,
  sourcePositions: Float32Array, sourceIndices: Uint32Array, materials: Uint8Array, origin: [number, number, number]) {
  const curbIdx: number[] = [], curbMat: number[] = [];
  materials.forEach((material, t) => {
    if (isCurbMaterial(material)) { curbIdx.push(...sourceIndices.subarray(t * 3, t * 3 + 3)); curbMat.push(material); }
  });
  if (!curbIdx.length) return null;
  // Compact the helper without duplicating the full ground vertex buffer in WASM.
  const remap = new Map<number, number>(), vertices: number[] = [];
  const indices = Uint32Array.from(curbIdx, index => {
    if (!remap.has(index)) { remap.set(index, remap.size); vertices.push(...sourcePositions.subarray(index * 3, index * 3 + 3)); }
    return remap.get(index)!;
  });
  const positions = Float32Array.from(vertices);
  const locator = new TriLocator(positions, indices, origin);
  return queries.add(collider, new R.TriMesh(positions, indices), point => {
    const t = locator.locate(point.x, point.y, point.z, 0.04);
    return t < 0 ? null : { material: curbMat[t], top: locator.top(t) };
  });
}

export function addCurbPrimitive(R: RapierApi, queries: CurbQueries, collider: Rapier.Collider, material: number) {
  if (!isCurbMaterial(material)) return null;
  return queries.add(collider, collider.shape, point => {
    // Sample the actual rotated primitive, not its axis-aligned bounds.
    const ray = new R.Ray({ x: point.x, y: point.y + 10, z: point.z }, { x: 0, y: -1, z: 0 });
    const hit = collider.castRay(ray, 20, true);
    return hit < 0 ? null : { material, top: point.y + 10 - hit };
  });
}
