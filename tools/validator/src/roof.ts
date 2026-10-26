import { MAT } from '../../../game/src/shared/col/index.ts';
import type { Loaded } from './context.ts';

/** Rapier 0.21 reports featureId=0 for these trimesh ray hits. Use the decoded
 * material triangles directly to distinguish standing on a roof from entering
 * a building footprint. */
export function roofSurface(loaded: Loaded[]) {
  const cell = 2;
  const roofs = new Map<string, number[][]>();
  for (const { tile } of loaded) {
    const [ox, oy, oz] = tile.header.origin;
    for (let t = 0; t < tile.header.triangleCount; t++) {
      if (tile.materials[t] !== MAT.roof) continue;
      const v = [0, 1, 2].map(c => {
        const i = tile.indices[t * 3 + c] * 3;
        return [ox + tile.positions[i], oy + tile.positions[i + 1], oz + tile.positions[i + 2]];
      });
      const x0 = Math.floor(Math.min(...v.map(p => p[0])) / cell), x1 = Math.floor(Math.max(...v.map(p => p[0])) / cell);
      const z0 = Math.floor(Math.min(...v.map(p => p[2])) / cell), z1 = Math.floor(Math.max(...v.map(p => p[2])) / cell);
      for (let x = x0; x <= x1; x++) for (let z = z0; z <= z1; z++) {
        const key = `${x},${z}`;
        const bucket = roofs.get(key) ?? [];
        bucket.push(v.flat());
        roofs.set(key, bucket);
      }
    }
  }
  return (x: number, feetY: number, z: number) => {
    for (const [ax, ay, az, bx, by, bz, cx, cy, cz] of roofs.get(`${Math.floor(x / cell)},${Math.floor(z / cell)}`) ?? []) {
      const d = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz);
      if (Math.abs(d) < 1e-8) continue;
      const u = ((bz - cz) * (x - cx) + (cx - bx) * (z - cz)) / d;
      const v = ((cz - az) * (x - cx) + (ax - cx) * (z - cz)) / d;
      if (u < -1e-6 || v < -1e-6 || u + v > 1 + 1e-6) continue;
      const y = u * ay + v * by + (1 - u - v) * cy;
      if (feetY >= y - 0.15) return true;
    }
    return false;
  };
}
