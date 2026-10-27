// Standalone buildings-layer validation before the assembler writes shipped .col files.
// Uses exported NPZ triangles (converted to JSON by turku3d.buildings.validate).
import RAPIER from '@dimforge/rapier3d-compat';
import { readFileSync, writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { Walker } from './kcc.ts';
import { loadFootprints, mulberry32, FootprintIndex } from './geo.ts';
import { GROUP, groups } from '../../../game/src/shared/col/index.ts';

export function facadeBaseAt(segments: number[][] | undefined, x: number, z: number, fallback: number): number {
  let nearest = Infinity, base = fallback;
  for (const [ax, az, bx, bz, height] of segments ?? []) {
    const dx = bx - ax, dz = bz - az;
    const length2 = dx * dx + dz * dz;
    if (length2 <= 1e-12) continue;
    const t = Math.max(0, Math.min(1, ((x - ax) * dx + (z - az) * dz) / length2));
    const distance = Math.hypot(x - ax - t * dx, z - az - t * dz);
    if (distance < nearest - .05) { nearest = distance; base = height; }
    else if (distance < nearest + .05) base = Math.min(base, height);
  }
  return nearest <= 1.5 ? base : fallback;
}

export async function validateBuildings(data: any, walkers = 2000) {
  const started = performance.now();
  await RAPIER.init();
  const world = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  // Group the actual clipped NPZ triangles by their source building. Isolated
  // trials prevent an enclosing building from masking a broken nested part.
  const colliders = new Map<string, RAPIER.Collider[]>();
  for (const tile of data.tiles) {
    if (tile.building_index.length * 3 !== tile.indices.length) throw new Error('invalid triangle provenance');
    const byOwner = new Map<string, number[]>();
    for (let face = 0; face < tile.building_index.length; face++) {
      const id = data.building_ids[tile.building_index[face]];
      if (typeof id !== 'string') throw new Error('unknown collision building');
      if (!byOwner.has(id)) byOwner.set(id, []);
      byOwner.get(id)!.push(...tile.indices.slice(face * 3, face * 3 + 3));
    }
    for (const [id, indices] of byOwner) {
      const collider = world.createCollider(RAPIER.ColliderDesc.trimesh(new Float32Array(tile.positions),
        new Uint32Array(indices)).setCollisionGroups(groups(GROUP.STATIC_SOLID, GROUP.CHARACTER)));
      collider.setEnabled(false);
      if (!colliders.has(id)) colliders.set(id, []);
      colliders.get(id)!.push(collider);
    }
  }
  const expected = loadFootprints(data.footprints);
  const missingShells = expected.filter(f => (!Number.isFinite(data.base_heights[f.id]) || !colliders.has(f.id))).map(f => f.id);
  const fps = expected.filter(f => Number.isFinite(data.base_heights[f.id]) && colliders.has(f.id));
  if (!fps.length) throw new Error('no building footprints to validate');
  let active: string | undefined;
  const rng = mulberry32(103);
  const walker = new Walker(RAPIER, world);
  // This pad isolates wall blocking from terrain availability. It is validation-only.
  const pad = world.createCollider(RAPIER.ColliderDesc.cuboid(1000, .5, 1000)
    .setCollisionGroups(groups(GROUP.STATIC_SOLID, GROUP.CHARACTER)));
  let entered = 0, endedInside = 0, trials = 0, rejectedStarts = 0;
  const failures: any[] = [];
  const perBuilding: Record<string, number> = {};
  for (let attempt = 0; trials < walkers && attempt < walkers * 100; attempt++) {
    const fp = fps[attempt % fps.length];
    const index = new FootprintIndex([fp]);
    if (active !== fp.id) {
      for (const collider of colliders.get(active ?? '') ?? []) collider.setEnabled(false);
      for (const collider of colliders.get(fp.id)!) collider.setEnabled(true);
      active = fp.id;
    }
    const ring = fp.rings[Math.floor(rng() * fp.rings.length)];
    const edge = Math.floor(rng() * ring.length);
    const a = ring[edge], b = ring[(edge + 1) % ring.length];
    const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (length < .8) continue;
    const t = .1 + .8 * rng();
    const x = a[0] + t * (b[0] - a[0]), z = a[1] + t * (b[1] - a[1]);
    let nx = -(b[1] - a[1]) / length, nz = (b[0] - a[0]) / length;
    if (index.containing(x + nx * .05, z + nz * .05)) { nx = -nx; nz = -nz; }
    const sx = x + nx * 1.2, sz = z + nz * 1.2;
    if (index.containing(sx, sz)) { rejectedStarts++; continue; }
    const base = facadeBaseAt(data.facade_segments?.[fp.id], x, z, data.base_heights[fp.id]);
    if (!Number.isFinite(base)) throw new Error(`missing shell/base height for ${fp.id}`);
    pad.setTranslation({ x: sx, y: base - .5, z: sz });
    walker.place(sx, base, sz);
    world.step();
    let crossed = false;
    for (let frame = 0; frame < 600; frame++) {
      walker.step(-nx * 1.5, -nz * 1.5);
      if (index.containing(walker.x, walker.z)) crossed = true;
    }
    const inside = index.containing(walker.x, walker.z);
    if (crossed) entered++;
    if (inside) endedInside++;
    if (crossed && failures.length < 20) failures.push({ building: fp.id, start: [sx, base, sz],
      end: [walker.x, walker.feetY, walker.z], endedInside: !!inside });
    perBuilding[fp.id] = (perBuilding[fp.id] ?? 0) + 1;
    trials++;
  }
  walker.free();
  world.free();
  return { pass: missingShells.length === 0 && trials === walkers && entered === 0 && fps.every(f => perBuilding[f.id] > 0),
    walkers: trials, requested: walkers, seconds_per_walker: 10, entered, endedInside, rejectedStarts,
    perBuilding, missingShells, untestedBuildings: fps.filter(f => !perBuilding[f.id]).map(f => f.id),
    scope: 'each building in isolation, using exported collision triangles and provenance',
    seed: 103, rapier: RAPIER.version(), failures, elapsed_ms: Math.round(performance.now() - started),
    terrain: 'validation-only horizontal pad at each shell base; integrated terrain tested after assembly' };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const result = await validateBuildings(JSON.parse(readFileSync(process.argv[2], 'utf8')));
  writeFileSync(process.argv[3], JSON.stringify(result, null, 2) + '\n');
  console.log(JSON.stringify(result));
  if (!result.pass) process.exitCode = 1;
}
