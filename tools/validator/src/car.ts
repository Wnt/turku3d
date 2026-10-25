import { GameVehicle } from '../../../game/src/physics/vehicle.ts';
import { CAR } from '../../../game/src/physics/constants.ts';

export interface Strike {
  source: string; x: number; z: number; nx: number; nz: number; low: number; high: number; step: number;
}
const coast = { throttle: 0, brake: 0, steer: 0, handbrake: false };

/** A scripted coast through a face, using the exact game chassis, suspension, input and update code.
 * The initial velocity is set once, never overwritten during the strike. */
export function driveStrike(R: any, world: any, st: Strike, speed: number, surfaceY: (x: number, z: number) => number) {
  const car = new GameVehicle(R, world);
  const distance = CAR.halfExt.x + 1.5;
  const x = st.x + st.nx * distance, z = st.z + st.nz * distance;
  const ground = surfaceY(x, z);
  if (!Number.isFinite(ground)) return { source: st.source, x: st.x, z: st.z, speed, status: 'fail', reason: 'no approach ground' };
  car.spawn({ x, y: ground + 0.8, z }, Math.atan2(st.nz, -st.nx));
  try {
    for (let f = 0; f < 90; f++) { car.update(1 / 60, coast); world.step(); }
    const initial = car.state()!;
    if (Math.hypot(initial.pos.x - x, initial.pos.z - z) > 0.5 || initial.wheels.filter(w => w.contact).length < 2)
      return { source: st.source, x: st.x, z: st.z, speed, status: 'fail', reason: 'unstable or obstructed approach' };
    car.chassis!.setLinvel({ x: -st.nx * speed, y: 0, z: -st.nz * speed }, true);
    const pitch = (q: { x: number; y: number; z: number; w: number }) => Math.asin(Math.max(-1, Math.min(1, 2 * (q.x * q.y + q.w * q.z)))) * 180 / Math.PI;
    const basePitch = pitch(initial.rot);
    let pitchSpikeDeg = 0, minSpeed = speed, maxPast = -distance, crossed = false;
    const frames = Math.ceil(((2 * distance + 2) / speed + 1) * 60);
    for (let f = 0; f < frames; f++) {
      car.update(1 / 60, coast); world.step();
      const s = car.state()!;
      const past = (st.x - s.pos.x) * st.nx + (st.z - s.pos.z) * st.nz;
      maxPast = Math.max(maxPast, past);
      pitchSpikeDeg = Math.max(pitchSpikeDeg, Math.abs(pitch(s.rot) - basePitch));
      minSpeed = Math.min(minSpeed, Math.max(0, -s.linvel.x * st.nx - s.linvel.z * st.nz));
      // Test at every frame, not just the final resting position.
      if (past > CAR.halfExt.x + 0.1) { crossed = true; break; }
    }
    return { source: st.source, x: st.x, z: st.z, step: st.step, speed, crossed, maxPast,
      pitchSpikeDeg, speedLoss: (speed - minSpeed) / speed, status: 'pass' };
  } finally { car.free(); }
}

export function checkCars(R: any, world: any, curbs: Strike[], barriers: Strike[], surfaceY: (x: number, z: number) => number) {
  const runs: any[] = [];
  for (const st of curbs) {
    if (!Number.isFinite(st.step)) { runs.push({ ...st, status: 'fail', reason: 'missing curb surface' }); continue; }
    for (const speed of [3, 6, 10, ...(st.step > 0.25 ? [2] : [])]) {
      const r = driveStrike(R, world, st, speed, surfaceY);
      const required = speed < 3 && st.step > 0.25;
      runs.push({ ...r, check: required ? 'stepBlock' : 'curbResponse',
        status: r.status === 'fail' || (required && (r.maxPast ?? -Infinity) > 0.05) ? 'fail' : 'pass',
        reportOnly: !required });
    }
  }
  for (const st of barriers) {
    const r = driveStrike(R, world, st, 30, surfaceY);
    // The chassis centre must stay on the approach side, including transient penetration.
    runs.push({ ...r, check: 'tunnelling', status: r.status === 'fail' || (r.maxPast ?? -Infinity) > 0.05 ? 'fail' : 'pass' });
  }
  return { status: runs.some(r => r.status === 'fail') ? 'fail' : runs.length ? 'pass' : 'skipped',
    curbResponse: 'report-only until the game curb-strike module lands (ARCHITECTURE 1.14: pitch >=2 degrees, loss 5–20%)',
    runs, failures: runs.filter(r => r.status === 'fail') };
}
