import { Vehicle } from '../../../game/src/vehicle/vehicle.ts';
import type { CurbQueries } from '../../../game/src/vehicle/curbQueries.ts';
import { CAR } from '../../../game/src/physics/constants.ts';

interface Strike {
  source: string; x: number; z: number; nx: number; nz: number; low: number; high: number; step: number;
}
const coast = { throttle: 0, brake: 0, steer: 0, handbrake: false };

/** Initial velocity is set once. Loss spans the tick before the first strike to the tick after
 * the last strike, matching W1-07's acceptance measurement (including wheelbase drag). */
function driveStrike(R: any, world: any, st: Strike, speed: number, surfaceY: (x: number, z: number) => number, surfaces: CurbQueries) {
  const distance = CAR.halfExt.x + 1.5;
  const x = st.x + st.nx * distance, z = st.z + st.nz * distance;
  const ground = surfaceY(x, z);
  if (!Number.isFinite(ground)) return { source: st.source, x: st.x, z: st.z, speed, status: 'fail', reason: 'no approach ground' };
  const car = new Vehicle(R, world, { x, y: ground + 0.8, z }, Math.atan2(st.nz, -st.nx), surfaces);
  try {
    for (let f = 0; f < 90; f++) { car.step(1 / 60, coast); world.step(); }
    const initial = car.state();
    if (Math.hypot(initial.pos.x - x, initial.pos.z - z) > 0.5 || initial.wheels.filter(w => w.contact).length < 2)
      return { source: st.source, x: st.x, z: st.z, speed, status: 'fail', reason: 'unstable or obstructed approach' };
    car.body.setLinvel({ x: -st.nx * speed, y: 0, z: -st.nz * speed }, true);
    const pitch = (q: { x: number; y: number; z: number; w: number }) => Math.asin(Math.max(-1, Math.min(1, 2 * (q.x * q.y + q.w * q.z)))) * 180 / Math.PI;
    const basePitch = pitch(initial.rot);
    let pitchSpikeDeg = 0, maxPast = -distance, crossed = false, strikes = 0, before = 0, after = 0;
    let previous = car.state();
    const frames = Math.ceil(((2 * distance + 2) / speed + 1) * 60);
    for (let f = 0; f < frames; f++) {
      car.step(1 / 60, speed < 3 && previous.curbStrike?.blocked ? { ...coast, throttle: 0.25 } : coast);
      world.step();
      const s = car.state();
      const past = (st.x - s.pos.x) * st.nx + (st.z - s.pos.z) * st.nz;
      maxPast = Math.max(maxPast, past);
      if (s.curbStrike) pitchSpikeDeg = Math.max(pitchSpikeDeg, Math.abs(pitch(s.rot) - basePitch));
      if (s.curbStrike && s.curbStrike.id !== previous.curbStrike?.id) {
        if (!strikes) before = Math.hypot(previous.linvel.x, previous.linvel.z);
        after = Math.hypot(s.linvel.x, s.linvel.z);
        strikes++;
      }
      previous = s;
      if (past > CAR.halfExt.x + 0.1) { crossed = true; break; }
    }
    return { source: st.source, x: st.x, z: st.z, step: st.step, speed, crossed, maxPast,
      pitchSpikeDeg, strikes, speedLoss: strikes ? 1 - after / before : null, status: 'pass' };
  } finally { world.removeVehicleController(car.controller); world.removeRigidBody(car.body); }
}

export function checkCars(R: any, world: any, curbs: Strike[], barriers: Strike[], surfaceY: (x: number, z: number) => number, surfaces: CurbQueries) {
  const runs: any[] = [];
  for (const st of curbs) {
    if (!Number.isFinite(st.step)) { runs.push({ ...st, status: 'fail', reason: 'missing curb surface' }); continue; }
    for (const speed of [3, 6, 10, ...(st.step > 0.25 ? [2] : [])]) {
      const r = driveStrike(R, world, st, speed, surfaceY, surfaces);
      const block = speed < 3 && st.step > 0.25;
      const response = !block && st.step >= 0.07 - 1e-6 && st.step <= 0.15 + 1e-6;
      const passed = block ? (r.maxPast ?? -Infinity) <= 0.05 : !response ||
        (r.crossed && r.strikes! > 0 && r.pitchSpikeDeg! >= 2 && r.speedLoss! >= 0.05 && r.speedLoss! <= 0.2);
      runs.push({ ...r, check: block ? 'stepBlock' : 'curbResponse', responseCriteriaApplicable: response,
        status: r.status === 'fail' || !passed ? 'fail' : 'pass' });
    }
  }
  for (const st of barriers) {
    const r = driveStrike(R, world, st, 30, surfaceY, surfaces);
    runs.push({ ...r, check: 'tunnelling', status: r.status === 'fail' || (r.maxPast ?? -Infinity) > 0.05 ? 'fail' : 'pass' });
  }
  return { status: runs.some(r => r.status === 'fail') ? 'fail' : runs.length ? 'pass' : 'skipped',
    thresholds: { pitchSpikeDegMin: 2, speedLossMin: 0.05, speedLossMax: 0.2, curbHeights: [0.07, 0.15], speeds: [3, 6, 10], blockHeightAbove: 0.25, blockSpeed: 2 },
    runs, failures: runs.filter(r => r.status === 'fail') };
}
