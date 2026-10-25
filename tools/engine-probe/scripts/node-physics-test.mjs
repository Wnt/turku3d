// Runs the exact same scenario as the browser, in Node (rapier3d-compat WASM).
import RAPIER from '@dimforge/rapier3d-compat';
import { createSim, runScenario } from '../src/sim.js';
await RAPIER.init();
const sim = createSim(RAPIER);
const t0 = performance.now();
const { samples, verdict } = runScenario(sim, 14, 30);
const ms = performance.now() - t0;
for (const s of samples) {
  const w = s.walkers;
  console.log(`t=${s.t.toFixed(2).padStart(5)} stairs x=${w.stairs.x.toFixed(2)} y=${w.stairs.feetY.toFixed(3)} | r15 x=${w.ramp15.x.toFixed(2)} y=${w.ramp15.feetY.toFixed(3)} | r55 x=${w.ramp55.x.toFixed(2)} y=${w.ramp55.feetY.toFixed(3)} | car x=${s.car.x.toFixed(2)} y=${s.car.y.toFixed(3)} v=${s.car.speed} wheels=${s.car.wheelsInContact}`);
}
console.log(JSON.stringify(verdict, null, 1));
console.log(`RAPIER ${RAPIER.version()} ; ${samples.at(-1).frame} frames in ${ms.toFixed(1)} ms (${(ms / samples.at(-1).frame).toFixed(3)} ms/frame)`);
