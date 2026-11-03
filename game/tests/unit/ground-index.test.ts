import { describe, expect, test } from 'vitest';
import fs from 'node:fs';
import { MeshoptDecoder } from 'meshoptimizer';
import { decodeGlb } from '../../src/world/decodeGlb';
import { readCol } from '../../src/physics/colTile';
import { GroundIndex } from '../../src/world/groundIndex';
import { parseRoadGraph, CAR, FOOT } from '../../src/gameplay/roadGraph';

import type { ColTile } from '../../src/physics/colTile';
import { MAT } from '../../src/shared/col/materials';

/** Real-tile parity (opt-in): a tile's collision cells, visual GLB and roads.json in GROUND_PARITY_DIR, as
 *  `col/<cell>.col`, `vis-<tile>.glb` and `roads-<tile>.json` (fetched from the farm: the tile manifest's
 *  `collision`, `visual` and `roads` URLs). 2026-11-03, t0_0_0: 7,357 shared points, p50 0, p95 8 mm, p99 14 mm,
 *  99.3% within 5 cm; 745 more points have ground (grass, gravel, kerbs the name filter missed); 0.9 vs 396 µs. */
const DIR = process.env.GROUND_PARITY_DIR ?? '';
const TILE = process.env.GROUND_PARITY_TILE ?? 't0_0_0';
type J = { meshes: { name?: string; primitives: { attributes: Record<string, number>; indices: number; material?: number }[] }[]; materials: { name?: string }[];
  accessors: { bufferView: number; byteOffset?: number; componentType: number; count: number; type: string }[];
  bufferViews: { byteOffset?: number; byteLength: number; byteStride?: number; extensions?: { EXT_meshopt_compression?: { byteOffset?: number; byteLength: number; byteStride: number; count: number; mode: string; filter?: string } } }[] };

test.skipIf(!DIR)('collision ground matches the old visual-mesh raycast on a real tile', async () => {
  await MeshoptDecoder.ready;
  const ground = new GroundIndex();
  for (const f of fs.readdirSync(`${DIR}/col`)) { const b = fs.readFileSync(`${DIR}/col/${f}`); const col = await readCol(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength)); ground.add(col); }
  const glb = fs.readFileSync(`${DIR}/vis-${TILE}.glb`);
  const { json, binary } = decodeGlb(glb.buffer.slice(glb.byteOffset, glb.byteOffset + glb.byteLength)); const j = json as unknown as J;
  const bin = new Uint8Array(binary!);
  const views = new Map<number, Uint8Array>();
  const view = (i: number) => {
    let out = views.get(i); if (out) return out;
    const bv = j.bufferViews[i], ext = bv.extensions?.EXT_meshopt_compression;
    if (ext) { out = new Uint8Array(ext.count * ext.byteStride); MeshoptDecoder.decodeGltfBuffer(out, ext.count, ext.byteStride, bin.subarray(ext.byteOffset ?? 0, (ext.byteOffset ?? 0) + ext.byteLength), ext.mode as never, (ext.filter ?? 'NONE') as never); }
    else out = bin.subarray(bv.byteOffset ?? 0, (bv.byteOffset ?? 0) + bv.byteLength);
    views.set(i, out); return out;
  };
  const rx = /ground|road|surface|pavement|asphalt|cobble|concrete|bridge.deck/i;
  const tris: number[] = [];
  for (const m of j.meshes) for (const p of m.primitives) {
    if (!rx.test(`${m.name ?? ''} ${p.material !== undefined ? j.materials[p.material].name ?? '' : ''}`)) continue;
    const pa = j.accessors[p.attributes.POSITION], ia = j.accessors[p.indices];
    const pv = view(pa.bufferView), iv = view(ia.bufferView);
    const stride = j.bufferViews[pa.bufferView].byteStride ?? j.bufferViews[pa.bufferView].extensions?.EXT_meshopt_compression?.byteStride ?? 12;
    const pos = new DataView(pv.buffer, pv.byteOffset + (pa.byteOffset ?? 0));
    const idx = ia.componentType === 5123 ? new Uint16Array(iv.buffer, iv.byteOffset + (ia.byteOffset ?? 0), ia.count) : new Uint32Array(iv.buffer, iv.byteOffset + (ia.byteOffset ?? 0), ia.count);
    for (let t = 0; t < ia.count; t += 3) for (let k = 0; k < 3; k++) { const o = idx[t + k] * stride; tris.push(pos.getFloat32(o, true), pos.getFloat32(o + 4, true), pos.getFloat32(o + 8, true)); }
  }
  // Old: first hit going down whose face normal points up (Raycaster over the named ground meshes).
  const old = (x: number, top: number, z: number, depth: number) => {
    let best: number | null = null;
    for (let o = 0; o < tris.length; o += 9) {
      const ax = tris[o], ay = tris[o + 1], az = tris[o + 2], bx = tris[o + 3], by = tris[o + 4], bz = tris[o + 5], cx = tris[o + 6], cy = tris[o + 7], cz = tris[o + 8];
      if (Math.max(ax, bx, cx) < x || Math.min(ax, bx, cx) > x || Math.max(az, bz, cz) < z || Math.min(az, bz, cz) > z) continue;
      const ux = bx - ax, uy = by - ay, uz = bz - az, vx = cx - ax, vy = cy - ay, vz = cz - az;
      const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx, n = Math.hypot(nx, ny, nz);
      const d = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz); if (Math.abs(d) < 1e-12 || n < 1e-12) continue;
      const w0 = ((bz - cz) * (x - cx) + (cx - bx) * (z - cz)) / d, w1 = ((cz - az) * (x - cx) + (ax - cx) * (z - cz)) / d, w2 = 1 - w0 - w1;
      if (w0 < 0 || w1 < 0 || w2 < 0) continue;
      const y = w0 * ay + w1 * by + w2 * cy;
      if (y > top || y < top - depth) continue;
      if (best === null || y > best) best = Math.abs(ny) / n > 0.5 ? y : best;
    }
    return best;
  };
  // Query points: car lanes and footways of the tile, every 1.5 m, plus wheel-offset points 0.8 m to each side.
  const graph = parseRoadGraph(JSON.parse(fs.readFileSync(`${DIR}/roads-${TILE}.json`, 'utf8')));
  const points: { x: number; z: number; car: boolean }[] = [];
  for (const e of graph.edges) {
    const car = !!(e.flags & CAR); if (!car && !(e.flags & FOOT)) continue;
    for (let i = 0; i + 1 < e.points.length / 2; i++) {
      const ax = e.points[2 * i], az = e.points[2 * i + 1], bx = e.points[2 * i + 2], bz = e.points[2 * i + 3], l = Math.hypot(bx - ax, bz - az);
      for (let s = 0; s < l; s += 1.5) { const x = ax + (bx - ax) * s / l, z = az + (bz - az) * s / l, nx = -(bz - az) / l, nz = (bx - ax) / l;
        if (x < graph.bbox[0] + 0.5 || x > graph.bbox[2] - 0.5 || z < graph.bbox[1] + 0.5 || z > graph.bbox[3] - 0.5) continue;
        for (const side of car ? [-0.8, 0, 0.8] : [0]) points.push({ x: x + nx * side, z: z + nz * side, car }); }
    }
  }
  const diffs: number[] = [], wheel: number[] = []; let both = 0, onlyOld = 0, onlyNew = 0, neither = 0;
  let tOld = 0, tNew = 0;
  for (const p of points) {
    let t = performance.now(); const a = old(p.x, 400, p.z, 800); tOld += performance.now() - t;
    t = performance.now(); const b = ground.ground(p.x, 400, p.z, 800); tNew += performance.now() - t;
    if (a !== null && b !== null) { both++; diffs.push(Math.abs(a - b)); const w = ground.ground(p.x, a + 2, p.z, 4); if (w !== null) wheel.push(Math.abs(w - a)); }
    else if (a !== null) onlyOld++; else if (b !== null) onlyNew++; else neither++;
  }
  const pct = (xs: number[], q: number) => { const s = [...xs].sort((u, v) => u - v); return +s[Math.min(s.length - 1, Math.floor(q * s.length))].toFixed(3); };
  expect(both).toBeGreaterThan(points.length * 0.8);
  expect(onlyOld).toBeLessThan(points.length * 0.01);
  expect(pct(diffs, .95)).toBeLessThan(0.02);
  expect(diffs.filter(d => d < .05).length / diffs.length).toBeGreaterThan(0.98);
  expect(wheel.filter(d => d < .05).length / wheel.length).toBeGreaterThan(0.98);
  expect(tNew * 50).toBeLessThan(tOld);
}, 300_000);

/** A collision cell from world-space quads (two triangles each) with one material per quad. */
function cell(id: string, i: number, j: number, quads: { x0: number; z0: number; x1: number; z1: number; y: number | ((x: number, z: number) => number); material: number; wall?: boolean }[]): ColTile {
  const positions: number[] = [], indices: number[] = [], materials: number[] = [], origin: [number, number, number] = [i * 62.5, 1, j * 62.5];
  for (const q of quads) {
    const h = (x: number, z: number) => typeof q.y === 'number' ? q.y : q.y(x, z);
    const corners = q.wall ? [[q.x0, q.y as number, q.z0], [q.x1, q.y as number, q.z0], [q.x1, (q.y as number) + 3, q.z0], [q.x0, (q.y as number) + 3, q.z0]]
      : [[q.x0, h(q.x0, q.z0), q.z0], [q.x1, h(q.x1, q.z0), q.z0], [q.x1, h(q.x1, q.z1), q.z1], [q.x0, h(q.x0, q.z1), q.z1]];
    const base = positions.length / 3;
    for (const [x, y, z] of corners) positions.push(x - origin[0], y - origin[1], z - origin[2]);
    indices.push(base, base + 1, base + 2, base, base + 2, base + 3);
    materials.push(q.material, q.material);
  }
  return { id, origin, tileI: i, tileJ: j, flags: 0, positions: Float32Array.from(positions), indices: Uint32Array.from(indices),
    materials: Uint8Array.from(materials), prims: [], meta: null };
}

describe('collision ground index', () => {
  const index = () => {
    const g = new GroundIndex();
    g.add(cell('c_0_0', 0, 0, [
      { x0: 0, z0: 0, x1: 62.5, z1: 62.5, y: 2, material: MAT.road },
      { x0: 10, z0: 20, x1: 30, z1: 30, y: 8, material: MAT.bridge_deck },
      { x0: 40, z0: 0, x1: 62.5, z1: 10, y: 2.12, material: MAT.sidewalk },
      { x0: 50, z0: 40, x1: 60, z1: 50, y: 14, material: MAT.roof },
      { x0: 0, z0: 50, x1: 20, z1: 50, y: 2, material: MAT.wall, wall: true },
      { x0: 0, z0: 55, x1: 10, z1: 62.5, y: (x: number) => 2 + x * 0.1, material: MAT.grass },
    ]));
    g.add(cell('c_1_0', 1, 0, [{ x0: 62.5, z0: 0, x1: 125, z1: 62.5, y: 3, material: MAT.road }]));
    return g;
  };

  test('finds the first walkable surface going down, like a downward ray', () => {
    const g = index();
    expect(g.ground(5, 400, 5, 800)).toBeCloseTo(2, 5);
    expect(g.ground(20, 400, 25, 800)).toBeCloseTo(8, 5); // the bridge deck from above
    expect(g.ground(20, 5, 25, 4)).toBeCloseTo(2, 5); // the road under it from just above the road
    expect(g.ground(50, 400, 5, 800)).toBeCloseTo(2.12, 5); // a kerb-height pavement
    expect(g.ground(5, 400, 58, 800)).toBeCloseTo(2.5, 4); // a slope
    expect(g.ground(5, 1.5, 5, 4)).toBeNull(); // nothing below the ray's start
    expect(g.ground(5, 30, 5, 4)).toBeNull(); // nothing within its depth
  });

  test('roofs and walls are not ground; seams and missing cells behave', () => {
    const g = index();
    expect(g.ground(55, 400, 45, 800)).toBeCloseTo(2, 5); // through the roof to the road
    expect(g.ground(10, 4, 50, 4)).toBeCloseTo(2, 5); // a vertical wall is never hit
    expect(g.ground(62.6, 400, 15, 800)).toBeCloseTo(3, 5);
    expect(g.ground(62.4, 400, 15, 800)).toBeCloseTo(2, 5);
    expect(g.ground(-5, 400, 5, 800)).toBeNull();
  });

  test('versions change only for the cell that changed', () => {
    const g = index();
    const a = g.version(5, 5), b = g.version(70, 5);
    g.add(cell('c_1_0', 1, 0, [{ x0: 62.5, z0: 0, x1: 125, z1: 62.5, y: 4, material: MAT.road }]));
    expect(g.version(5, 5)).toBe(a);
    expect(g.version(70, 5)).not.toBe(b);
    expect(g.ground(70, 400, 5, 800)).toBeCloseTo(4, 5);
    g.remove('c_1_0');
    expect([g.version(70, 5), g.ground(70, 400, 5, 800)]).toEqual([-1, null]);
  });
});
