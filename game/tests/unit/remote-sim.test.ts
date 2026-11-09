import { expect, test } from 'vitest';
import * as R from '@dimforge/rapier3d';
import { PhysicsWorld } from '../../src/physics/world';
import { GROUP } from '../../src/shared/col/materials';
import { collisionTileAt, collisionTileId } from '../../src/core/origin';
import { DT } from '../../src/physics/constants';
import type { RemoteBody, RemoteCarState, VehicleInput } from '../../src/physics/protocol';

// Nearby remote cars run as real vehicles (physics/world.ts REMOTE_SIM): driven by their owner's controls, pulled to the
// owner's state predicted to the present, and hit like any parked car.

function flatWorld() {
  const pw = new PhysicsWorld(R);
  pw.world.createCollider(R.ColliderDesc.cuboid(400, 1, 400).setTranslation(0, -1, 0).setCollisionGroups(((GROUP.STATIC_SOLID & 0xffff) << 16) | 0xffff));
  // The flat ground stands for resident collision tiles around the origin.
  const resident = (pw as unknown as { residentCells: Set<string> }).residentCells;
  for (let x = -200; x <= 200; x += 10) for (let z = -200; z <= 200; z += 10) {
    const c = collisionTileAt(x, z);
    resident.add(collisionTileId(c.i, c.j));
  }
  pw.spawnVehicle({ x: 0, y: 1, z: 0 }, 0, { id: 'me', kind: 'car' });
  pw.driveVehicle('me');
  for (let k = 0; k < 90; k++) pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
  return pw;
}

const yawQuat = (yaw: number) => ({ x: 0, y: Math.sin(yaw / 2), z: 0, w: Math.cos(yaw / 2) });
function remote(state: Omit<RemoteCarState, 'rot' | 'spin'> & { yaw: number }, extra: Partial<RemoteBody> = {}): RemoteBody {
  const { yaw, ...rest } = state;
  return { id: 'bot', pos: state.pos, yaw, mode: 'car', vel: state.vel, model: 'test', tuning: null,
    state: { ...rest, rot: yawQuat(yaw), spin: { x: 0, y: 0, z: 0 } }, ...extra };
}
const sim = (pw: PhysicsWorld) => pw.remoteCarDraws().find(c => c.id === 'bot');

test('a nearby remote car driving past is simulated here and stays on its owner\'s path predicted to the present', () => {
  const pw = flatWorld(), y = pw.vehicleState()!.pos.y;
  // The owner drives along +z at 10 m/s (chassis +X forward: yaw -90°), 15 m beside us; every third tick a state 100 ms old.
  const speed = 10, input: VehicleInput = { throttle: 0.25, steer: 0, brake: 0, handbrake: false };
  let worst = 0;
  for (let k = 0; k < 180; k++) {
    const now = k * DT, sent = Math.floor(k / 3) * 3 * DT - 0.1;
    pw.setRemotes([remote({ st: Math.round(sent * 1000) + 1000, age: now - sent, pos: { x: 15, y, z: -45 + speed * sent }, vel: { x: 0, y: 0, z: speed }, input, yaw: -Math.PI / 2 })]);
    pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
    const car = sim(pw);
    expect(car).toBeDefined();
    if (k > 30) worst = Math.max(worst, Math.hypot(car!.pos.x - 15, car!.pos.z - (-45 + speed * (now + DT))));
  }
  expect(worst).toBeLessThan(0.5);
  expect(pw.remoteProxies()).toEqual([]);
  pw.world.free();
});

test('a simulated remote car is hit like a parked car of its class, then settles back onto its owner\'s state', () => {
  const pw = flatWorld(), me = () => pw.vehicleState()!, y = me().pos.y;
  const parked = () => remote({ st: 1000, age: 0.1, pos: { x: 40, y, z: 0 }, vel: { x: 0, y: 0, z: 0 }, input: null, yaw: 0 });
  let before = 0, hit = -1;
  for (let k = 0; k < 600 && hit < 0; k++) {
    pw.setRemotes([parked()]); pw.fixedStep(null, { throttle: 1, steer: 0, brake: 0, handbrake: false });
    if (sim(pw)!.pos.x - me().pos.x < 4.4) hit = k; else before = me().linvel.x;
  }
  expect(hit).toBeGreaterThan(0);
  expect(before).toBeGreaterThan(6);
  let pushed = 0;
  for (let k = 0; k < 12; k++) {
    pw.setRemotes([parked()]); pw.fixedStep(null, { throttle: 0, steer: 0, brake: 1, handbrake: false });
    pushed = Math.max(pushed, sim(pw)!.pos.x - 40);
  }
  // Momentum split by the masses: neither a wall's dead stop nor a pass-through; the car it hit moves.
  expect(me().linvel.x).toBeLessThan(before * 0.8);
  expect(pushed).toBeGreaterThan(0.2);
  // Its owner reports it never moved: it goes back there, without a placement, within a few seconds.
  for (let k = 0; k < 240; k++) { pw.setRemotes([parked()]); pw.fixedStep(null, { throttle: 0, steer: 0, brake: 1, handbrake: false }); }
  expect(Math.abs(sim(pw)!.pos.x - 40)).toBeLessThan(0.3);
  expect(sim(pw)!.snaps).toBe(0);
  pw.world.free();
});

test('a remote car far away, of an unknown model or without owner state stays a proxy; a jump is placed', () => {
  const pw = flatWorld(), y = pw.vehicleState()!.pos.y;
  const far = remote({ st: 1000, age: 0.05, pos: { x: 90, y, z: 0 }, vel: { x: 0, y: 0, z: 0 }, input: null, yaw: 0 });
  pw.setRemotes([far]); pw.fixedStep(null, null);
  expect(sim(pw)).toBeUndefined();
  pw.setRemotes([{ ...far, state: undefined }]); pw.fixedStep(null, null);
  expect(sim(pw)).toBeUndefined();
  const near = remote({ st: 1000, age: 0.05, pos: { x: 30, y, z: 0 }, vel: { x: 0, y: 0, z: 0 }, input: null, yaw: 0 });
  pw.setRemotes([near]); pw.fixedStep(null, null);
  expect(sim(pw)).toBeDefined();
  // A teleport (respawn) is placed at once and counted, so the renderer hides the jump.
  pw.setRemotes([remote({ st: 2000, age: 0.05, pos: { x: 30, y, z: 25 }, vel: { x: 0, y: 0, z: 0 }, input: null, yaw: 0 })]); pw.fixedStep(null, null);
  expect(sim(pw)!.snaps).toBe(1);
  expect(Math.abs(sim(pw)!.pos.z - 25)).toBeLessThan(0.2);
  // Leaving the room removes it.
  pw.setRemotes([]); pw.fixedStep(null, null);
  expect(sim(pw)).toBeUndefined();
  pw.world.free();
});
