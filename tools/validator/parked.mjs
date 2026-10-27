// Load shipped .col primitives through the shared Rapier builder and probe both collision groups.
// Usage: node tools/validator/parked.mjs <collision directory>
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import RAPIER from '@dimforge/rapier3d-compat';
import { MeshoptDecoder } from 'meshoptimizer';
import { readCol, buildTileColliders, GROUP, groups } from '../../game/src/shared/col/index.ts';
await RAPIER.init();
await MeshoptDecoder.ready;
let count = 0;
for (const name of readdirSync(process.argv[2]).filter(n => n.endsWith('.col'))) {
  const tile = readCol(new Uint8Array(readFileSync(join(process.argv[2], name))), { decoder: MeshoptDecoder });
  const world = new RAPIER.World({ x: 0, y: 0, z: 0 });
  const built = buildTileColliders(RAPIER, world, tile);
  world.step();
  for (const { collider, cls, kind } of built.colliders) {
    if (kind !== 'box' || cls !== 'solid') throw new Error('Expected solid parked-car box');
    const p = collider.translation();
    const half = collider.halfExtents();
    for (const group of [GROUP.CHARACTER, GROUP.VEHICLE]) {
      const filter = groups(group, GROUP.STATIC_SOLID);
      const ray = new RAPIER.Ray({ x: p.x, y: p.y + half.y + 2, z: p.z }, { x: 0, y: -1, z: 0 });
      const hit = world.castRay(ray, 5, true, undefined, filter);
      if (!hit || Math.abs(hit.timeOfImpact - 2) > .002) throw new Error(`Missing collision ${name}`);
      // Sweep the actual player capsule / driven-car chassis into the parked vehicle.
      const shape = group === GROUP.CHARACTER ? new RAPIER.Capsule(.5, .3) : new RAPIER.Cuboid(2.1, .55, .9);
      const sweep = world.castShape({ x: p.x - 12, y: p.y, z: p.z }, { x: 0, y: 0, z: 0, w: 1 },
        { x: 1, y: 0, z: 0 }, shape, 0, 15, true, undefined, filter);
      if (!sweep || sweep.time_of_impact >= 12) throw new Error(`Unblocked shape ${name}`);
    }
    count++;
  }
  world.free();
}
if (!count) throw new Error('No parked colliders tested');
console.log(JSON.stringify({ pass: true, boxes: count, rays: count * 2, shapeSweeps: count * 2 }));
