import { afterEach, expect, test, vi } from 'vitest';
import * as R from '@dimforge/rapier3d';
import * as THREE from 'three/webgpu';
import { writeCol } from '../../scripts/colWriter.mjs';
import { PhysicsWorld, DT, COVERAGE_HOLD_SECONDS } from '../../src/physics/world';
import { PlayableArea } from '../../src/gameplay/recovery';
import { SafeReset } from '../../src/gameplay/safeReset';

const idle = { vx: 0, vz: 0, jump: false };
const east = { ...idle, vx: 6 };
const worlds: PhysicsWorld[] = [];
afterEach(() => { worlds.splice(0).forEach(pw => pw.world.free()); });
const col = (id: string) => {
  const [, i, j] = id.split('_').map(Number);
  const b = writeCol({ i, j, positions: new Float32Array([0, 2, 0, 0, 2, 62.5, 62.5, 2, 0, 62.5, 2, 62.5]),
    indices: new Uint32Array([0, 1, 2, 2, 1, 3]), materials: new Uint8Array([1, 1]), prims: [], flags: 0, meta: {} });
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer;
};
async function setup(car: boolean) {
  const pw = new PhysicsWorld(R); worlds.push(pw);
  await pw.loadTile('c_3_2', col('c_3_2'));
  pw.world.step();
  pw.spawnCharacter({ x: 220, y: 2.05, z: 156 });
  if (car) {
    pw.setCharacterEnabled(false);
    pw.spawnVehicle({ x: 247, y: 2.8, z: 156 }, 0);
    // No throttle: the swept velocity represents momentum/gravity on a slope.
    pw.vehicle!.body.setLinvel({ x: 12, y: -1, z: 0 }, true);
  } else pw.setCharacterEnabled(true, { x: 249.7, y: 2.05, z: 156 });
  const input = car ? idle : east;
  const step = (seconds = DT) => {
    for (let i = 0; i < Math.round(seconds / DT); i++) pw.step(DT, input, null);
  };
  return { pw, step };
}

for (const car of [true, false]) {
  const actor = car ? 'player' : 'character';
  const label = car ? 'sliding driven car' : 'character';
  test(`${label}: collision gone from under the actor requests immediate safe reset, preserving the seat`, async () => {
    const { pw, step } = await setup(car);
    const collision = { c_3_2: { h: [2, 2] } };
    const frame = { mode: car ? 'car' as const : 'foot' as const, drivenId: car ? 'player' : null,
      feet: new THREE.Vector3(249.7, 2.05, 156), vel: new THREE.Vector3(), grounded: false, facing: 0, flying: false };
    const physics = {
      get latest() { return { coverageBlock: pw.coverageBlock }; },
      loaded: new Map([['c_3_2', {}]]), placement: { seq: 0, actor: '', pos: { x: 0, y: 0, z: 0 } },
      raycast: vi.fn(async () => ({ point: { y: 2 } })),
      resetVehicle: vi.fn((pos, yaw, id) => pw.resetVehicle(pos, yaw, id)),
      setCharacterEnabled: vi.fn((enabled, pos) => pw.setCharacterEnabled(enabled, pos)),
    };
    const hud = { recovered: vi.fn() }, onReset = vi.fn();
    const reset = new SafeReset(new PlayableArea(collision), { collision } as never, physics as never,
      { update: vi.fn() } as never, hud as never, onReset);
    // Real safe samples from before the edge; stale contacts during the hold must not replace them.
    for (let time = 0; time <= 4; time++) reset.monitor.update({ time, x: 220 + time, y: 2.8, z: 156,
      heading: 0.5, inVehicle: car, grounded: true, loaded: true, steady: true, vy: 0, waterDepth: null }, 2);
    const target = reset.monitor.target();
    pw.unloadTile('c_3_2');
    step();
    expect(pw.coverageBlock).toEqual({ actor, recover: true });
    reset.update(DT, frame, false);
    await vi.waitFor(() => expect(reset.monitor.resets).toBe(1));
    expect(onReset).toHaveBeenCalledWith(frame.mode, target.heading);
    expect(pw.coverageBlock).toBeNull();
    if (car) {
      expect(physics.resetVehicle).toHaveBeenCalledWith({ x: target.x, y: 2.75, z: target.z }, expect.any(Number), 'player');
      expect(physics.setCharacterEnabled).not.toHaveBeenCalled();
      expect(pw.vehicle!.body.bodyType()).toBe(R.RigidBodyType.Dynamic);
      expect(pw.vehicleState()!.linvel).toEqual({ x: 0, y: 0, z: 0 });
      expect(pw.vehicleState()!.rot.x).toBe(0); expect(pw.vehicleState()!.rot.z).toBe(0);
      expect(pw.characterState()!.enabled).toBe(false);
    } else expect(physics.setCharacterEnabled).toHaveBeenCalledWith(true, { x: target.x, y: 2.05, z: target.z });
    await pw.loadTile('c_3_2', col('c_3_2'));
    step();
    expect(pw.coverageBlock).toBeNull();
  });

  test(`${label}: active load holds briefly, then resumes when collision arrives`, async () => {
    const { pw, step } = await setup(car);
    pw.edges.setKnown(['c_4_2'], []);
    pw.setTileLoading('c_4_2', true);
    step(1);
    expect(pw.coverageBlock).toEqual({ actor, recover: false });
    if (car) expect(pw.vehicle!.body.bodyType()).toBe(R.RigidBodyType.Fixed);
    await pw.loadTile('c_4_2', col('c_4_2'));
    pw.setTileLoading('c_4_2', false);
    const x = car ? pw.vehicleState()!.pos.x : pw.characterState()!.pos.x;
    step();
    expect(pw.coverageBlock).toBeNull();
    expect(car ? pw.vehicleState()!.pos.x : pw.characterState()!.pos.x).toBeGreaterThan(x);
    if (car) {
      expect(pw.vehicle!.body.bodyType()).toBe(R.RigidBodyType.Dynamic);
      expect(pw.vehicleState()!.linvel.x).toBeGreaterThan(10);
    }
  });

  test(`${label}: stalled load times out`, async () => {
    const { pw, step } = await setup(car);
    pw.edges.setKnown(['c_4_2'], []);
    pw.setTileLoading('c_4_2', true);
    step(COVERAGE_HOLD_SECONDS);
    expect(pw.coverageBlock).toEqual({ actor, recover: false });
    step(2 * DT);
    expect(pw.coverageBlock).toEqual({ actor, recover: true });
    // The request is repeated in snapshots until placement, even if several frames are skipped.
    step(1);
    expect(pw.coverageBlock).toEqual({ actor, recover: true });
  });

  test(`${label}: a known cell between requests (queued or retrying) gets the bounded wait, not an instant reset`, async () => {
    const { pw, step } = await setup(car);
    pw.edges.setKnown(['c_4_2'], []);
    pw.setTileLoading('c_4_2', true); step();
    expect(pw.coverageBlock?.recover).toBe(false);
    pw.setTileLoading('c_4_2', false); step();
    expect(pw.coverageBlock).toEqual({ actor, recover: false });
    step(COVERAGE_HOLD_SECONDS);
    expect(pw.coverageBlock).toEqual({ actor, recover: true });
  });

  test(`${label}: a neighbour with no collision source is closed by its edge wall, never held or reset`, async () => {
    // Capture 20261105: a shared car on a resident cell 3.0 m from an unpublished row (z < 0) was reset every
    // time its 3.1 m footprint circle touched that row, before the body could even reach the wall.
    const { pw, step } = await setup(car);
    step(2);
    expect(pw.coverageBlock).toBeNull();
    if (car) expect(pw.vehicle!.body.bodyType()).toBe(R.RigidBodyType.Dynamic);
    const x = car ? pw.vehicleState()!.pos.x : pw.characterState()!.pos.x;
    expect(x).toBeLessThan(250);
    expect(x).toBeGreaterThan(car ? 245 : 249);
    // A cell whose load failed for good leaves the known set (collisionStreaming.ts): the wall returns, no hold.
    pw.edges.setKnown(['c_4_2'], []); step();
    expect(pw.coverageBlock?.recover).toBe(false);
    pw.edges.setKnown([], ['c_4_2']); step();
    expect(pw.coverageBlock).toBeNull();
  });
}

test('parked vehicles can remain held without requesting a player reset', async () => {
  const { pw, step } = await setup(true);
  pw.edges.setKnown(['c_4_2'], []);
  pw.driveVehicle(null); step(4);
  expect(pw.vehicle!.body.bodyType()).toBe(R.RigidBodyType.Fixed);
  expect(pw.coverageBlock).toBeNull();
  pw.driveVehicle('player'); step();
  expect(pw.coverageBlock).toEqual({ actor: 'player', recover: false });
  step(COVERAGE_HOLD_SECONDS);
  expect(pw.coverageBlock).toEqual({ actor: 'player', recover: true });
});

test('safe reset never returns the player to a sample whose collision left the manifest, and logs the real reason', async () => {
  const { pw, step } = await setup(true);
  const collision: Record<string, { h: number[] }> = { c_3_2: { h: [2, 2] } };
  const frame = { mode: 'car' as const, drivenId: 'player', feet: new THREE.Vector3(247, 2.8, 156), vel: new THREE.Vector3(),
    grounded: false, facing: 0, flying: false };
  const loaded = new Map([['c_3_2', {}]]);
  const physics = {
    get latest() { return { coverageBlock: pw.coverageBlock }; }, loaded,
    placement: { seq: 0, actor: '', pos: { x: 0, y: 0, z: 0 } },
    raycast: vi.fn(async () => ({ point: { y: 2 } })),
    resetVehicle: vi.fn((pos, yaw, id) => pw.resetVehicle(pos, yaw, id)), setCharacterEnabled: vi.fn(),
  };
  const info = vi.spyOn(console, 'info').mockImplementation(() => {});
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const reset = new SafeReset(new PlayableArea(collision), { collision } as never, physics as never,
    { update: vi.fn() } as never, { recovered: vi.fn() } as never, vi.fn());
  for (let time = 0; time <= 4; time++) reset.monitor.update({ time, x: 220 + time, y: 2.8, z: 156, heading: 0,
    inVehicle: true, grounded: true, loaded: true, steady: true, vy: 0, waterDepth: null }, 2);
  // The draft that held every sample is dropped: no sample has collision to return to.
  pw.unloadTile('c_3_2'); loaded.delete('c_3_2'); delete collision.c_3_2;
  step();
  expect(pw.coverageBlock).toEqual({ actor: 'player', recover: true });
  for (let k = 0; k < 5; k++) reset.update(DT, frame, false);
  await new Promise(resolve => setTimeout(resolve, 10));
  expect(physics.resetVehicle).not.toHaveBeenCalled();
  expect(reset.monitor.resets).toBe(0);
  expect(warn).toHaveBeenCalledTimes(1);
  // Collision returns: the reset happens and says why.
  collision.c_3_2 = { h: [2, 2] }; loaded.set('c_3_2', {});
  reset.update(DT, frame, false);
  await vi.waitFor(() => expect(reset.monitor.resets).toBe(1));
  expect(info.mock.calls.at(-1)![0]).toMatch(/^\[recovery\] reset \(collision\) from /);
  info.mockRestore(); warn.mockRestore();
});

test('a fast driven car is eased to a stop before collision that has not arrived, never frozen at its edge, and drives on once it lands', async () => {
  const pw = new PhysicsWorld(R); worlds.push(pw);
  for (let i = 0; i <= 5; i++) await pw.loadTile(`c_${i}_2`, col(`c_${i}_2`));
  pw.world.step();
  pw.spawnCharacter({ x: 10, y: 2.05, z: 156 });
  pw.setCharacterEnabled(false);
  pw.spawnVehicle({ x: 20, y: 2.8, z: 156 }, 0);
  pw.vehicle!.body.setLinvel({ x: 30, y: 0, z: 0 }, true);
  pw.edges.setKnown(['c_6_2'], []);
  pw.setTileLoading('c_6_2', true);
  const drive = { throttle: 1, steer: 0, brake: 0, handbrake: false };
  const speeds: number[] = [];
  let eased = false;
  for (let k = 0; k < Math.round(20 / DT); k++) {
    pw.step(DT, idle, drive);
    expect(pw.vehicle!.body.bodyType()).toBe(R.RigidBodyType.Dynamic);
    const v = pw.vehicle!.body.linvel();
    speeds.push(Math.hypot(v.x, v.z));
    eased ||= pw.vehicleStates().find(s => s.id === 'player')!.eased !== undefined;
  }
  expect(eased).toBe(true);
  expect(pw.coverageBlock).toBeNull();
  expect(Math.max(...speeds)).toBeGreaterThan(24);
  expect(speeds.at(-1)!).toBeLessThan(0.5);
  // Smooth: above walking pace no step loses more than an emergency stop (15 m/s²), and no step at all loses 0.5 m/s;
  // the hold took the whole speed away in one step.
  for (let k = 1; k < speeds.length; k++) {
    expect(speeds[k - 1] - speeds[k]).toBeLessThan(0.5);
    if (speeds[k] > 3) expect(speeds[k - 1] - speeds[k]).toBeLessThan(15 * DT);
  }
  const x = pw.vehicle!.body.translation().x;
  expect(x).toBeGreaterThan(340);
  expect(x).toBeLessThan(375);
  await pw.loadTile('c_6_2', col('c_6_2'));
  pw.setTileLoading('c_6_2', false);
  for (let k = 0; k < Math.round(3 / DT); k++) pw.step(DT, idle, drive);
  expect(pw.vehicle!.body.translation().x).toBeGreaterThan(380);
});

test('cells ahead of a moving driven car install before nearer cells behind it', async () => {
  const pw = new PhysicsWorld(R); worlds.push(pw);
  await pw.loadTile('c_3_2', col('c_3_2'));
  pw.world.step();
  pw.spawnCharacter({ x: 190, y: 2.05, z: 156 });
  pw.setCharacterEnabled(false);
  pw.spawnVehicle({ x: 200, y: 2.8, z: 156 }, 0);
  const order: string[] = [];
  const request = (id: string) => pw.requestTile(id, col(id), r => { if (!(r instanceof Error)) order.push(id); });
  // Behind: c_1_2 is 75 m away; ahead: c_5_2 is 112.5 m away.
  request('c_1_2'); request('c_5_2');
  pw.vehicle!.body.setLinvel({ x: 20, y: 0, z: 0 }, true);
  for (let k = 0; k < 40 && order.length < 2; k++) pw.pumpInstalls(0);
  expect(order).toEqual(['c_5_2', 'c_1_2']);
  // Standing still, nearest first.
  pw.unloadTile('c_1_2'); pw.unloadTile('c_5_2'); order.length = 0;
  pw.vehicle!.body.setLinvel({ x: 0, y: 0, z: 0 }, true);
  request('c_1_2'); request('c_5_2');
  for (let k = 0; k < 40 && order.length < 2; k++) pw.pumpInstalls(0);
  expect(order).toEqual(['c_1_2', 'c_5_2']);
});

test('a tile still coming keeps its edge wall, but the driven car is eased to a stop before it instead of hitting it at speed', async () => {
  const drive = { throttle: 1, steer: 0, brake: 0, handbrake: false };
  const run = async (coming: boolean) => {
    const pw = new PhysicsWorld(R); worlds.push(pw);
    for (let i = 0; i <= 5; i++) await pw.loadTile(`c_${i}_2`, col(`c_${i}_2`));
    pw.world.step();
    pw.spawnCharacter({ x: 10, y: 2.05, z: 156 });
    pw.setCharacterEnabled(false);
    pw.spawnVehicle({ x: 20, y: 2.8, z: 156 }, 0);
    pw.vehicle!.body.setLinvel({ x: 24, y: 0, z: 0 }, true);
    // c_6_2 is unpublished (no wall opening: not known); its tile is being built when `coming`.
    if (coming) pw.setComing(['c_6_2'], []);
    let worstDrop = 0, prev = 24;
    for (let k = 0; k < Math.round(20 / DT); k++) {
      pw.step(DT, idle, drive);
      const v = pw.vehicle!.body.linvel(), speed = Math.hypot(v.x, v.z);
      worstDrop = Math.max(worstDrop, prev - speed); prev = speed;
    }
    return { worstDrop, x: pw.vehicle!.body.translation().x, block: pw.coverageBlock };
  };
  const wall = await run(false), eased = await run(true);
  // Against the wall the car loses several m/s in one step; eased, it never does and stops short of the wall.
  expect(wall.worstDrop).toBeGreaterThan(3);
  expect(eased.worstDrop).toBeLessThan(0.5);
  expect(eased.x).toBeLessThan(372); expect(eased.x).toBeGreaterThan(340);
  expect(eased.block).toBeNull();
});
