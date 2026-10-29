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
  test(`${label}: never-loaded cell requests immediate safe reset, preserving the seat`, async () => {
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

  test(`${label}: stalled load times out; failed/deferred loads do not wait`, async () => {
    const { pw, step } = await setup(car);
    pw.setTileLoading('c_4_2', true);
    step(COVERAGE_HOLD_SECONDS);
    expect(pw.coverageBlock).toEqual({ actor, recover: false });
    step(2 * DT);
    expect(pw.coverageBlock).toEqual({ actor, recover: true });
    // The request is repeated in snapshots until placement, even if several frames are skipped.
    step(1);
    expect(pw.coverageBlock).toEqual({ actor, recover: true });
  });

  test(`${label}: a failed active request hands over immediately instead of waiting for a retry`, async () => {
    const { pw, step } = await setup(car);
    pw.edges.setKnown(['c_4_2'], []);
    pw.setTileLoading('c_4_2', true); step();
    expect(pw.coverageBlock?.recover).toBe(false);
    pw.setTileLoading('c_4_2', false); step();
    expect(pw.coverageBlock).toEqual({ actor, recover: true });
  });
}

test('parked vehicles can remain held without requesting a player reset', async () => {
  const { pw, step } = await setup(true);
  pw.driveVehicle(null); step(4);
  expect(pw.vehicle!.body.bodyType()).toBe(R.RigidBodyType.Fixed);
  expect(pw.coverageBlock).toBeNull();
  pw.driveVehicle('player'); step();
  expect(pw.coverageBlock).toEqual({ actor: 'player', recover: true });
});
