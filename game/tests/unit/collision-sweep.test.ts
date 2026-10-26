import { test, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import * as RAPIER from '@dimforge/rapier3d';
import { PhysicsWorld } from '../../src/physics/world';

test.skipIf(!process.env.COLLISION_SWEEP_DIST)('published spawn grid walking sweep', async () => {
  const root = process.env.COLLISION_SWEEP_DIST!;
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json'), 'utf8'));
  const [cx, cy, cz] = manifest.spawn.position as number[];
  const footprints = manifest.spawn.footprints as { coordinates: number[][][] }[];
  const distanceToBuilding = (x: number, z: number) => Math.min(...footprints.map((poly) => {
    const ring = poly.coordinates[0];
    let hit = false;
    let distance = Infinity;
    for (let a = 0, b = ring.length - 1; a < ring.length; b = a++) {
      const [ax, az] = ring[a], [bx, bz] = ring[b];
      if ((az > z) !== (bz > z) && x < (bx - ax) * (z - az) / (bz - az) + ax) hit = !hit;
      const length2 = (bx - ax) ** 2 + (bz - az) ** 2;
      if (length2 === 0) continue;
      const t = Math.max(0, Math.min(1, ((x - ax) * (bx - ax) + (z - az) * (bz - az)) / length2));
      distance = Math.min(distance, Math.hypot(x - ax - t * (bx - ax), z - az - t * (bz - az)));
    }
    return hit ? 0 : distance;
  }));
  const world = new PhysicsWorld(RAPIER);
  for (const [id, entry] of Object.entries<{ url: string }>(manifest.collision)) {
    const [i, j] = id.slice(2).split('_').map(Number);
    if (i < Math.floor((cx - 45) / 62.5) || i > Math.floor((cx + 45) / 62.5)
      || j < Math.floor((cz - 45) / 62.5) || j > Math.floor((cz + 45) / 62.5)) continue;
    const buffer = fs.readFileSync(path.join(root, entry.url));
    await world.loadTile(id, buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength));
  }
  world.world.step();
  const stops: { start: number[]; end: number[]; direction: number[]; requested_m: number;
    actual_m: number; material: number | null; ground_material: number;
    tile: string | null; reason: string | null }[] = [];
  let attempted = 0, skipped = 0;
  const starts: [number, number][] = [];
  for (let dx = -40; dx <= 40; dx += 5) for (let dz = -40; dz <= 40; dz += 5) starts.push([dx, dz]);
  for (let dx = -12; dx <= 12; dx += 2) for (let dz = -12; dz <= 12; dz += 2) starts.push([dx, dz]);
  for (const [dx, dz] of starts) {
    if (Math.hypot(dx, dz) > 40) continue;
    const x = cx + dx, z = cz + dz;
    const hit = world.raycast({ x, y: cy + 1, z }, { x: 0, y: -1, z: 0 }, 4);
    if (!hit || hit.point.y < cy - 2 || hit.point.y > cy + 0.5 || distanceToBuilding(x, z) < 0.35
      || ![1, 2, 5, 6, 7, 8, 9, 10, 14].includes(hit.material)) { skipped++; continue; }
    for (const [vx, vz] of [[2, 0], [-2, 0], [0, 2], [0, -2]] as const) {
      attempted++;
      world.spawnCharacter({ x, y: hit.point.y + 0.04, z });
      for (let step = 0; step < 120; step++) world.fixedStep({ vx, vz, jump: false }, null);
      const end = world.characterState()!.pos;
      const actual = Math.hypot(end.x - x, end.z - z);
      if (actual < 2.5) {
        const rays = [0.25, 0.6, 0.9, 1.35].map((height) => world.raycast(
          { x: end.x, y: end.y + height, z: end.z }, { x: vx, y: 0, z: vz }, 1.2));
        const ray = rays.filter((item) => item !== null).sort((a, b) => a!.toi - b!.toi)[0];
        const nearby = [[1, 0], [-1, 0], [0, 1], [0, -1]].flatMap(([nx, nz]) =>
          [0.4, 0.8].map((h) => world.raycast({ x: end.x, y: end.y + h, z: end.z }, { x: nx, y: 0, z: nz }, 1.2)));
        const blocker = [ray, ...nearby].find((item) => item && [3, 15, 19, 22, 25, 26, 27, 28, 29, 30, 31].includes(item.material));
        const reason = distanceToBuilding(end.x, end.z) < 0.42 ? 'building' : blocker ? `visible_material_${blocker.material}` : null;
        stops.push({ start: [x, hit.point.y, z], end: [end.x, end.y, end.z],
          direction: [vx / 2, vz / 2],
          requested_m: 4, actual_m: Number(actual.toFixed(3)), material: ray?.material ?? null,
          ground_material: hit.material,
          tile: ray?.tile ?? null, reason });
      }
    }
  }
  const report = { spawn: [cx, cy, cz], attempted, skipped, stops,
    unexplained: stops.filter((stop) => stop.reason === null) };
  const output = process.env.COLLISION_SWEEP_REPORT!;
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, JSON.stringify(report, null, 2) + '\n');
  expect(attempted).toBeGreaterThan(0);
  expect(report.unexplained).toEqual([]);
}, 180_000);
