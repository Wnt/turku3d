// Collision scale benchmark: how big a trimesh / heightfield tile can Rapier WASM take?
import RAPIER from '@dimforge/rapier3d-compat';
import { buildBigGrid } from '../src/level.js';
await RAPIER.init();
const mb = () => (process.memoryUsage().arrayBuffers / 1048576);
const rss = () => (process.memoryUsage().rss / 1048576);
for (const [n, flagName] of [[200, 'none'], [500, 'none'], [500, 'FIX_INTERNAL_EDGES'], [1000, 'none'], [1000, 'FIX_INTERNAL_EDGES'], [1400, 'none']]) {
  const world = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  const g = buildBigGrid(n, 0.25);
  const tris = g.indices.length / 3;
  const m0 = rss(); const t0 = performance.now();
  let col;
  try {
    const flags = flagName === 'none' ? 0 : RAPIER.TriMeshFlags[flagName];
    col = world.createCollider(RAPIER.ColliderDesc.trimesh(g.positions, g.indices, flags));
  } catch (e) { console.log(`n=${n} tris=${tris} FAILED: ${e.message}`); continue; }
  const tBuild = performance.now() - t0;
  // character controller cost walking over the mesh
  const body = world.createRigidBody(RAPIER.RigidBodyDesc.kinematicPositionBased().setTranslation(5, 1.0, 5));
  const cap = world.createCollider(RAPIER.ColliderDesc.capsule(0.6, 0.3), body);
  const cc = world.createCharacterController(0.02); cc.enableAutostep(0.25, 0.15, false); cc.enableSnapToGround(0.3);
  world.step();
  const t1 = performance.now(); let vy = 0;
  for (let i = 0; i < 600; i++) { cc.computeColliderMovement(cap, { x: 0.03, y: -0.01, z: 0.02 }); const m = cc.computedMovement(); const t = body.translation(); body.setNextKinematicTranslation({ x: t.x + m.x, y: t.y + m.y, z: t.z + m.z }); world.step(); }
  const tStep = (performance.now() - t1) / 600;
  // raycasts (e.g. vehicle wheels / bullets)
  const ray = new RAPIER.Ray({ x: 0, y: 5, z: 0 }, { x: 0, y: -1, z: 0 });
  const t2 = performance.now();
  for (let i = 0; i < 100000; i++) { ray.origin.x = (i * 7.3) % (n * 0.25); ray.origin.z = (i * 3.1) % (n * 0.25); world.castRay(ray, 10, true); }
  const tRay = (performance.now() - t2) / 100;
  console.log(`trimesh n=${n} (${(n*0.25)|0} m tile @25cm) tris=${tris.toLocaleString()} flags=${flagName} build=${tBuild.toFixed(0)}ms rssDelta=${(rss()-m0).toFixed(0)}MB  charStep=${tStep.toFixed(3)}ms  raycast=${(tRay*1000).toFixed(2)}us`);
  world.free();
}
// Heightfield equivalent (no vertical walls possible, but 4 bytes/sample)
for (const n of [1000, 2000]) {
  const world = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  const h = new Float32Array((n + 1) * (n + 1));
  for (let i = 0; i < h.length; i++) h[i] = (i * 2654435761 % 100) < 2 ? 0.12 : 0;
  const m0 = rss(); const t0 = performance.now();
  world.createCollider(RAPIER.ColliderDesc.heightfield(n, n, h, { x: n * 0.25, y: 1, z: n * 0.25 }));
  console.log(`heightfield ${n}x${n} (${n*0.25} m @25cm) build=${(performance.now()-t0).toFixed(0)}ms rssDelta=${(rss()-m0).toFixed(0)}MB`);
  world.free();
}
