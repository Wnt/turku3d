import { expect, test } from 'vitest';
import * as R from '@dimforge/rapier3d';
import { writeCol } from '../../scripts/colWriter.mjs';
import { PhysicsWorld, DT } from '../../src/physics/world';
import { readCol } from '../../src/physics/colTile';
import { compactPiece, MAX_PIECE_TRIS, planCell } from '../../src/physics/tileInstall';
import { MAT } from '../../src/shared/col/materials';
import type { TileStats } from '../../src/physics/protocol';

/** A dense bumpy 62.5 m ground cell (~39k triangles, FIX_INTERNAL_EDGES), like the city's heaviest cells. */
function denseCell(i: number, j: number, n = 140, y = 2) {
  const step = 62.5 / n, positions: number[] = [], indices: number[] = [];
  for (let a = 0; a <= n; a++) for (let b = 0; b <= n; b++) positions.push(a * step, y + 0.05 * Math.sin(a * 0.7) * Math.cos(b * 0.9), b * step);
  for (let a = 0; a < n; a++) for (let b = 0; b < n; b++) {
    const v = a * (n + 1) + b;
    indices.push(v, v + 1, v + n + 1, v + 1, v + n + 2, v + n + 1);
  }
  const materials = new Uint8Array(indices.length / 3).fill(MAT.road);
  for (let t = 0; t < materials.length; t += 97) materials[t] = MAT.grass;
  const b = writeCol({ i, j, positions: new Float32Array(positions), indices: new Uint32Array(indices), materials, prims: [], flags: 1, meta: {} });
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer;
}

test('a dense cell splits into compact pieces that keep every triangle and material', async () => {
  const col = await readCol(denseCell(0, 0));
  const plan = planCell(col);
  const tris = plan.pieces.reduce((n, p) => n + (p.kind === 'mesh' ? p.tris.length : 0), 0);
  expect(tris).toBe(col.materials.length);
  expect(plan.pieces.length).toBeGreaterThanOrEqual(Math.ceil(tris / MAX_PIECE_TRIS));
  let area = 0, grass = 0;
  const remap = new Int32Array(col.positions.length / 3).fill(-1);
  for (const ref of plan.pieces) {
    if (ref.kind !== 'mesh') continue;
    const p = compactPiece(col, ref, remap);
    expect(p.indices.length / 3).toBeLessThanOrEqual(MAX_PIECE_TRIS);
    // Compacted: no piece carries the whole cell's vertex buffer.
    expect(p.positions.length / 3).toBeLessThan(col.positions.length / 3 / 4);
    for (let t = 0; t < p.indices.length; t += 3) {
      const [a, b, c] = [p.indices[t], p.indices[t + 1], p.indices[t + 2]].map(v => [p.positions[3 * v], p.positions[3 * v + 2]]);
      area += Math.abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2;
    }
    grass += p.materials.filter(m => m === MAT.grass).length;
  }
  expect(area).toBeCloseTo(62.5 * 62.5, 0);
  expect(grass).toBe(col.materials.filter(m => m === MAT.grass).length);
});

test('a queued dense cell installs in short pieces between steps, nearest first, and joins the world whole', async () => {
  const pw = new PhysicsWorld(R);
  await pw.loadTile('c_0_0', denseCell(0, 0));
  pw.spawnVehicle({ x: 20, y: 3, z: 20 }, 0);
  for (let k = 0; k < 30; k++) pw.step(DT, { vx: 0, vz: 0, jump: false }, { throttle: 0.5, steer: 0, brake: 0, handbrake: false });
  // Reference: the same cell built in one go (the old worker path).
  const ref = new PhysicsWorld(R);
  const r0 = performance.now(); await ref.loadTile('c_1_0', denseCell(1, 0)); const whole = performance.now() - r0;
  const order: string[] = [];
  const results = new Map<string, TileStats | Error>();
  for (const id of ['c_3_0', 'c_1_0']) pw.requestTile(id, denseCell(Number(id.split('_')[1]), 0), r => { results.set(id, r); order.push(id); });
  let worst = 0, ticks = 0;
  while (results.size < 2 && ticks < 2000) {
    const t0 = performance.now();
    pw.pumpInstalls(2);
    worst = Math.max(worst, performance.now() - t0);
    // Not resident (nor colliding) until complete.
    if (!results.has('c_1_0')) expect(pw.raycast({ x: 62.5 + 30, y: 10, z: 30 }, { x: 0, y: -1, z: 0 }, 20)).toBeNull();
    pw.step(DT, { vx: 0, vz: 0, jump: false }, { throttle: 0.5, steer: 0, brake: 0, handbrake: false });
    ticks++;
  }
  expect(order).toEqual(['c_1_0', 'c_3_0']);
  const stats = results.get('c_1_0') as TileStats;
  expect(stats.pieces).toBeGreaterThanOrEqual(20);
  expect(ticks).toBeGreaterThan(stats.pieces! / 4);
  // One piece is a small fraction of the whole cell's build (whole ~20-50 ms on an M1 Max).
  expect(worst).toBeLessThan(Math.max(6, whole * 0.4));
  const hit = pw.raycast({ x: 62.5 + 30, y: 10, z: 30 }, { x: 0, y: -1, z: 0 }, 20);
  expect(hit?.tile).toBe('c_1_0');
  expect(hit?.material).toBe(MAT.road);
});

test('unloading a queued cell cancels it; a queued replacement keeps the old cell until it is complete', async () => {
  const pw = new PhysicsWorld(R);
  const base = pw.world.colliders.len();
  let cancelled: TileStats | Error | null = null;
  pw.requestTile('c_0_0', denseCell(0, 0), r => { cancelled = r; });
  for (let k = 0; k < 4; k++) pw.pumpInstalls(1);
  expect(pw.unloadTile('c_0_0')).toBe(true);
  expect(cancelled).toBeInstanceOf(Error);
  expect(pw.world.colliders.len()).toBe(base);
  pw.pumpInstalls(50);
  expect(pw.hasTile('c_0_0')).toBe(false);

  await pw.loadTile('c_0_0', denseCell(0, 0, 40, 2));
  let replaced: TileStats | Error | null = null;
  pw.requestTile('c_0_0', denseCell(0, 0, 140, 5), r => { replaced = r; });
  pw.pumpInstalls(1);
  pw.world.step();
  expect(replaced).toBeNull();
  expect(pw.raycast({ x: 30, y: 10, z: 30 }, { x: 0, y: -1, z: 0 }, 20)?.point.y).toBeCloseTo(2, 0);
  for (let k = 0; k < 500 && !replaced; k++) { pw.pumpInstalls(2); pw.world.step(); }
  expect(replaced).not.toBeInstanceOf(Error);
  expect(pw.raycast({ x: 30, y: 10, z: 30 }, { x: 0, y: -1, z: 0 }, 20)?.point.y).toBeCloseTo(5, 0);
});
