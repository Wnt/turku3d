import RAPIER from '@dimforge/rapier3d-compat';
import { NodeIO } from '@gltf-transform/core';
import fs from 'fs';
await RAPIER.init();
const curbs = JSON.parse(fs.readFileSync('curbs.json')).filter(c => Math.abs(c.step) >= 0.08 && Math.abs(c.step) <= 0.25);
const io = new NodeIO();
for (const f of process.argv.slice(2)) {
  const prim = (await io.read(f)).getRoot().listMeshes()[0].listPrimitives()[0];
  const V = prim.getAttribute('POSITION').getArray(), I = new Uint32Array(prim.getIndices().getArray());
  const world = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  const t0 = performance.now();
  world.createCollider(RAPIER.ColliderDesc.trimesh(new Float32Array(V), I));
  world.step(); const tBuild = performance.now() - t0; console.log("curbs", curbs.length);
  const rayH = (x, z) => { const h = world.castRay(new RAPIER.Ray({ x, y: 100, z }, { x: 0, y: -1, z: 0 }), 200, true); return h ? 100 - h.timeOfImpact : NaN; };
  const res = {};
  for (const autostep of [false, true]) {
    let crossed = 0, n = 0;
    for (const c of curbs) {
      const s = Math.sign(c.step), dx = c.n[0] * s, dz = -c.n[1] * s; // walk toward high side
      const sx = c.p[0] - dx * 0.9, sz = -c.p[1] - dz * 0.9, gy = rayH(sx, sz); if (!isFinite(gy)) continue;
      const shape = RAPIER.ColliderDesc.capsule(0.6, 0.3).setTranslation(sx, gy + 0.92, sz);
      const col = world.createCollider(shape); world.step();
      const cc = world.createCharacterController(0.02);
      cc.setMaxSlopeClimbAngle(45 * Math.PI / 180); cc.setMinSlopeSlideAngle(50 * Math.PI / 180);
      cc.enableSnapToGround(0.3);
      if (autostep) cc.enableAutostep(0.25, 0.1, true);
      let p = col.translation();
      for (let k = 0; k < 120; k++) { // 2 s at 60 Hz, 1.0 m/s
        cc.computeColliderMovement(col, { x: dx / 60, y: -0.05, z: dz / 60 });
        const m = cc.computedMovement(); p = { x: p.x + m.x, y: p.y + m.y, z: p.z + m.z }; col.setTranslation(p);
      }
      const along = (p.x - sx) * dx + (p.z - sz) * dz; // start was 0.9 m before curb
      n++; if (along > 1.3) crossed++; if (process.env.DBG) console.log(autostep, c.step.toFixed(3), along.toFixed(2), (p.y-gy).toFixed(2));
      world.removeCharacterController(cc); world.removeCollider(col, false);
    }
    res[autostep ? 'autostep0.25' : 'noAutostep'] = `${crossed}/${n} crossed`;
  }
  // wheel bump: ball r=0.33 m rolling at 5 m/s across the curb (low->high), record max upward velocity
  let vmax = [], n2 = 0;
  for (const c of curbs) {
    const s = Math.sign(c.step), dx = c.n[0] * s, dz = -c.n[1] * s;
    const sx = c.p[0] - dx * 1.5, sz = -c.p[1] - dz * 1.5, gy = rayH(sx, sz); if (!isFinite(gy)) continue;
    const b = world.createRigidBody(RAPIER.RigidBodyDesc.dynamic().setTranslation(sx, gy + 0.34, sz).setLinvel(dx * 5, 0, dz * 5).setCcdEnabled(true));
    world.createCollider(RAPIER.ColliderDesc.ball(0.33).setDensity(500).setFriction(0.9), b);
    let m = 0; for (let k = 0; k < 60; k++) { world.step(); m = Math.max(m, b.linvel().y); }
    vmax.push(m); world.removeRigidBody(b);
  }
  vmax.sort((a, b) => a - b);
  console.log(f.split('/').pop(), `tris=${I.length / 3} build=${tBuild.toFixed(0)}ms`, JSON.stringify(res), `wheel max vy median=${vmax[vmax.length >> 1].toFixed(2)} m/s (n=${vmax.length})`);
}
