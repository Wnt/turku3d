// Can the GPU-less server pre-bake Rapier collision (BVH included) per tile? Compare build vs snapshot restore.
import RAPIER from '@dimforge/rapier3d-compat';
import { buildBigGrid } from '../src/level.js';
import { gzipSync } from 'node:zlib';
await RAPIER.init();
const g = buildBigGrid(1000, 0.25);
const w = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
let t = performance.now();
w.createCollider(RAPIER.ColliderDesc.trimesh(g.positions, g.indices, RAPIER.TriMeshFlags.FIX_INTERNAL_EDGES));
w.step();
console.log('build 2M tris (FIX_INTERNAL_EDGES):', (performance.now() - t).toFixed(0), 'ms');
t = performance.now(); const snap = w.takeSnapshot();
console.log('snapshot:', (snap.length / 1048576).toFixed(1), 'MB raw,', (gzipSync(snap, { level: 6 }).length / 1048576).toFixed(1), 'MB gzip, take', (performance.now() - t).toFixed(0), 'ms');
t = performance.now(); const w2 = RAPIER.World.restoreSnapshot(snap);
const hit = w2.castRay(new RAPIER.Ray({ x: 7, y: 10, z: 7 }, { x: 0, y: -1, z: 0 }), 20, true);
console.log('restore:', (performance.now() - t).toFixed(0), 'ms; raycast after restore hit y=', hit && (10 - hit.timeOfImpact).toFixed(3));
console.log('raw input arrays:', ((g.positions.byteLength + g.indices.byteLength) / 1048576).toFixed(1), 'MB');
