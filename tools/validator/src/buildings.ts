// Standalone buildings-layer validation before the assembler writes shipped .col files.
// Uses exported NPZ triangles (converted to JSON by turku3d.buildings.validate).
import RAPIER from '@dimforge/rapier3d-compat';
import { readFileSync, writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { Walker } from './kcc.ts';
import { loadFootprints, mulberry32, FootprintIndex } from './geo.ts';
import { GROUP, groups } from '../../../game/src/shared/col/index.ts';

export async function validateBuildings(data: any, walkers = 2000) {
  const started = performance.now();
  await RAPIER.init();
  const world = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  for (const tile of data.tiles) {
    world.createCollider(RAPIER.ColliderDesc.trimesh(new Float32Array(tile.positions),
      new Uint32Array(tile.indices)).setCollisionGroups(groups(GROUP.STATIC_SOLID, GROUP.CHARACTER)));
  }
  const expected = loadFootprints(data.footprints);
  const missingShells = expected.filter(f => !Number.isFinite(data.base_heights[f.id])).map(f => f.id);
  const fps = expected.filter(f => Number.isFinite(data.base_heights[f.id]));
  if (!fps.length) throw new Error('no building footprints to validate');
  const index = new FootprintIndex(fps);
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
    const base = data.base_heights[fp.id];
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
    seed: 103, rapier: RAPIER.version(), failures, elapsed_ms: Math.round(performance.now() - started),
    terrain: 'validation-only horizontal pad at each shell base; integrated terrain tested after assembly' };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const result = await validateBuildings(JSON.parse(readFileSync(process.argv[2], 'utf8')));
  writeFileSync(process.argv[3], JSON.stringify(result, null, 2) + '\n');
  console.log(JSON.stringify(result));
  if (!result.pass) process.exitCode = 1;
}
