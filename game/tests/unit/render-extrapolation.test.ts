import { expect, test } from 'vitest';
import { CharacterInterpolation, MAX_EXTRAPOLATION, VehicleInterpolation } from '../../src/actors/interpolation';
import type { CharacterState, VehicleState } from '../../src/physics/protocol';

const STEP = 1 / 60, SPEED = 18, YAW_RATE = 0.6;
const yawQuat = (yaw: number) => ({ x: 0, y: Math.sin(yaw / 2), z: 0, w: Math.cos(yaw / 2) });
/** A car on a circle-free straight at 18 m/s turning at 0.6 rad/s, at physics time s. */
const car = (s: number): VehicleState => ({ pos: { x: SPEED * s, y: 1, z: 0 }, rot: yawQuat(YAW_RATE * s), linvel: { x: SPEED, y: 0, z: 0 },
  speed: SPEED, forwardSpeed: SPEED, curbStrike: null, wheels: [{ pos: { x: 1, y: -0.4, z: 0.8 }, steer: 0, rotation: 10 * s, contact: true, suspension: 0 }] });
const yawOf = (q: { y: number; w: number }) => 2 * Math.atan2(q.y, q.w);

test('a late physics snapshot extrapolates the car, then blends back without a snap or backwards motion', () => {
  const render = new VehicleInterpolation();
  let tick = 0, physics = 0, wall = 0;
  const drawn: { t: number; x: number; yaw: number }[] = [];
  const frame = () => { const v = render.sample(wall)!; drawn.push({ t: wall, x: v.pos.x, yaw: yawOf(v.rot) }); };
  const publish = (substeps: number) => { for (let k = 0; k < substeps; k++) { physics += STEP; tick++; } render.push(car(physics), car(physics - STEP), tick, 0, wall); };
  // 30 fps rendering of 60 Hz physics, two substeps per snapshot.
  for (let f = 0; f < 30; f++) { wall += 2 * STEP; publish(2); frame(); }
  // The worker goes silent for 180 ms (the capture's stall), then catches up with its five-substep cap.
  const silentFrom = wall;
  for (let f = 0; f < 5; f++) { wall += 2 * STEP; frame(); }
  publish(5);
  for (let f = 0; f < 30; f++) { wall += 2 * STEP; publish(2); frame(); }
  // Every frame moves forward, at no less than half speed while a correction fades (the stalled capture showed
  // 2-5 identical poses at 6-18 m/s).
  for (let k = 1; k < drawn.length; k++) expect(drawn[k].x - drawn[k - 1].x).toBeGreaterThan(0.45 * SPEED * 2 * STEP);
  // During the silence the car ran ahead along its velocity and turn rate, by at most MAX_EXTRAPOLATION.
  const silent = drawn.filter(d => d.t > silentFrom && d.t <= silentFrom + 5 * 2 * STEP);
  const last = car(physics - 2 * 30 * STEP - 5 * STEP);
  expect(silent.at(-1)!.x).toBeLessThanOrEqual(last.pos.x + SPEED * (MAX_EXTRAPOLATION + 1e-6));
  expect(silent.at(-1)!.yaw).toBeGreaterThan(yawOf(last.rot));
  // Afterwards it converges on the interpolated physics pose.
  const end = drawn.at(-1)!;
  expect(Math.abs(end.x - (physics - STEP) * SPEED)).toBeLessThan(0.15);
  expect(Math.abs(end.yaw - YAW_RATE * (physics - STEP))).toBeLessThan(0.01);
});

test('extrapolation stops at its limit, and teleports are not smoothed', () => {
  const walker = new CharacterInterpolation();
  const at = (x: number): CharacterState => ({ pos: { x, y: 0, z: 0 }, vel: { x: 5, y: 0, z: 0 }, grounded: true, enabled: true, groundMaterial: 0 });
  walker.push(at(1), at(1 - 5 * STEP), 1, 0, 0);
  expect(walker.sample(STEP)!.pos.x).toBeCloseTo(1);
  // One step late it still holds; later it moves on at the last velocity.
  expect(walker.sample(2 * STEP)!.pos.x).toBeCloseTo(1);
  expect(walker.sample(STEP + 0.05)!.pos.x).toBeCloseTo(1 + 5 * (0.05 - STEP));
  expect(walker.sample(10)!.pos.x).toBeCloseTo(1 + 5 * MAX_EXTRAPOLATION);
  walker.push(at(40), at(1), 2, 0, 10);
  expect(walker.sample(10)!.pos.x).toBe(40);
  const render = new VehicleInterpolation();
  render.push(car(1), car(1 - STEP), 1, 0, 0);
  render.sample(0.1);
  const moved = car(1); moved.pos = { x: 500, y: 1, z: 0 };
  render.push(moved, car(1), 2, 0, 0.1);
  expect(render.sample(0.1)!.pos.x).toBe(500);
});

/** 60 Hz worker steps with timer jitter, each state reaching the renderer 0-4 ms after its step; a renderer at
 *  `fps` with ±15% frame jitter samples the car on a steady 1 rad/s turn. Returns the render yaw per frame. */
function steadyTurn(fps: number, extrapolate: boolean) {
  let seed = 7;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const turning = (s: number): VehicleState => ({ ...car(s), rot: yawQuat(s) });
  const render = new VehicleInterpolation();
  let wall = 0, physics = 0, tick = 0, nextStep = 0, previous = NaN;
  const frames: { wall: number; yaw: number }[] = [];
  for (let f = 0; f < 400; f++) {
    wall += (1 / fps) * (0.85 + 0.3 * rnd());
    while (nextStep + 0.006 <= wall) {
      physics += STEP; tick++;
      const published = nextStep + 0.004 * rnd();
      if (published <= wall) render.push(turning(physics), turning(physics - STEP), tick, 0, published);
      nextStep += STEP * (0.95 + 0.1 * rnd());
    }
    const raw = yawOf(render.sample(wall, extrapolate)!.rot);
    const yaw = Number.isNaN(previous) ? raw : previous + Math.atan2(Math.sin(raw - previous), Math.cos(raw - previous));
    frames.push({ wall, yaw }); previous = yaw;
  }
  return frames.slice(20);
}

test.each([35, 60, 120])('the render yaw turns every frame of a steady turn at %i fps, as plain interpolation does', fps => {
  const frames = steadyTurn(fps, true), plain = steadyTurn(fps, false);
  const rates = frames.slice(1).map((f, k) => (f.yaw - frames[k].yaw) / (f.wall - frames[k].wall));
  // Never a stalled frame, never a step: no frame turns at under 15% or over 200% of the true 1 rad/s.
  for (const rate of rates) { expect(rate).toBeGreaterThan(0.15); expect(rate).toBeLessThan(2); }
  // Short holds (a snapshot up to one step late) are as rare as before extrapolation existed.
  expect(rates.filter(r => r < 0.5).length).toBeLessThanOrEqual(Math.ceil(rates.length * 0.05));
  // On-time snapshots never extrapolate or blend: the pose is exactly the interpolation between snapshots.
  frames.forEach((f, k) => expect(f.yaw).toBeCloseTo(plain[k].yaw, 9));
});
