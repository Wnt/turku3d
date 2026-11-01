import { expect, test, vi } from 'vitest';
import { ParkedVehicles } from '../../src/world/parkedVehicles';
import { draftManifest, type ParkedVehicle } from '../../src/world/manifest';
import type { Quat, V3, VehicleSpec, VehicleState } from '../../src/physics/protocol';
import { QUALITY } from '../../src/render/quality';

const settle = () => new Promise(r => setTimeout(r, 0));
const slot = (object_id: string, x: number, z: number, model = 'sedan'): ParkedVehicle => ({ object_id, model, color: 'blue',
  x, y: 2, z, yaw: 0.3, width: 1.85, length: 4.55, height: 1.48, clear_front_m: 10, clear_rear_m: 10 });

/** Worker stand-in: bodies appear in `latest` as soon as they are spawned. */
function world(cars: ParkedVehicle[], cap: number) {
  const bodies = new Map<string, VehicleState>();
  const physics = {
    loaded: { has: () => true }, latest: { vehicles: [] as VehicleState[] },
    spawnVehicle: vi.fn((pos: V3, yaw: number, spec: Partial<VehicleSpec>) => {
      const rot: Quat = spec.rot ?? { x: 0, y: Math.sin(yaw / 2), z: 0, w: Math.cos(yaw / 2) };
      bodies.set(spec.id!, { id: spec.id, pos: { ...pos }, rot, linvel: { x: 0, y: 0, z: 0 }, speed: 0 } as VehicleState);
      physics.latest.vehicles = [...bodies.values()];
    }),
    removeVehicle: vi.fn((id: string) => { bodies.delete(id); physics.latest.vehicles = [...bodies.values()]; }),
  };
  const manifest = draftManifest('http://localhost/'); manifest.vehicles = cars;
  const stream = new ParkedVehicles(manifest, physics as never, { load: async () => {} },
    { addTruck: vi.fn(async () => {}), retainTrucks: vi.fn() }, cap);
  let now = 0;
  const at = async (x: number, z: number) => { stream.update(x, z, null, now += 1000); await settle(); };
  return { physics, bodies, stream, manifest, at, physical: () => new Set(bodies.keys()) };
}
const nearest = (cars: ParkedVehicle[], x: number, z: number, k: number) => new Set([...cars]
  .sort((a, b) => Math.hypot(a.x - x, a.z - z) - Math.hypot(b.x - x, b.z - z)).slice(0, k).map(c => c.object_id));
/** `n` slots on a 3 m grid centred on (cx, cz). */
const cluster = (prefix: string, cx: number, cz: number, n: number) => {
  const side = Math.ceil(Math.sqrt(n));
  return Array.from({ length: n }, (_, i) => slot(`${prefix}${i}`, cx + (i % side - (side - 1) / 2) * 3, cz + (Math.floor(i / side) - (side - 1) / 2) * 3));
};
const tiers = (['mobile', 'low', 'medium', 'high'] as const).map(tier => [tier, QUALITY[tier].parkedBodies] as const);

test('quality tiers cap parked bodies at 24 / 40 / 60 / 80', () => {
  expect(tiers.map(([, k]) => k)).toEqual([24, 40, 60, 80]);
});

test.each(tiers)('%s: of 150 slots within 120 m exactly the nearest %i get bodies', async (_tier, k) => {
  // Golden-angle spiral: distinct distances from 2 to 113.75 m.
  const cars = Array.from({ length: 150 }, (_, i) => {
    const r = 2 + 0.75 * i, a = i * 2.399963;
    return slot(`s${i}`, 500 + r * Math.cos(a), 300 + r * Math.sin(a));
  });
  expect(cars.every(c => Math.hypot(c.x - 500, c.z - 300) < 120)).toBe(true);
  const { physics, physical, at } = world(cars, k);
  await at(500, 300);
  expect(physics.spawnVehicle).toHaveBeenCalledTimes(k);
  expect(physical()).toEqual(nearest(cars, 500, 300, k));
});

test.each(tiers)('%s: moving re-ranks bodies to the new nearest %i, evicting by rank inside 170 m', async (_tier, k) => {
  const here = cluster('a', 0, 0, 150 - k), there = cluster('b', 100, 0, k), cars = [...here, ...there];
  expect(cars.every(c => Math.hypot(c.x, c.z) < 120)).toBe(true);
  const { physics, bodies, physical, stream, at } = world(cars, k);
  await at(0, 0);
  expect(physical()).toEqual(nearest(cars, 0, 0, k));
  // A body that was pushed keeps that pose once it becomes visual, and resumes from it.
  const pushed = here.reduce((a, b) => Math.hypot(a.x, a.z) <= Math.hypot(b.x, b.z) ? a : b).object_id;
  const moved = { pos: { x: bodies.get(pushed)!.pos.x + 1, y: 2.7, z: bodies.get(pushed)!.pos.z - 0.5 }, rot: { x: 0.1, y: 0.2, z: 0, w: Math.sqrt(0.95) } };
  Object.assign(bodies.get(pushed)!, moved); physics.latest.vehicles = [...bodies.values()];
  await at(100, 0);
  expect(physical()).toEqual(new Set(there.map(c => c.object_id)));
  const evicted = physics.removeVehicle.mock.calls.map(([id]) => cars.find(c => c.object_id === id)!);
  expect(evicted.length).toBeGreaterThan(0);
  expect(evicted.every(c => c.object_id.startsWith('a') && Math.hypot(c.x - 100, c.z) < 170)).toBe(true);
  const car = cars.find(c => c.object_id === pushed)!;
  expect(stream.restingPose(car)).toEqual(moved);
  await at(0, 0);
  expect(physical()).toEqual(nearest(cars, 0, 0, k));
  expect(physics.spawnVehicle.mock.calls.filter(([, , spec]) => spec.id === pushed).at(-1))
    .toEqual([moved.pos, car.yaw - Math.PI / 2, expect.objectContaining({ id: pushed, rot: moved.rot, kind: 'car' })]);
});

test.each([
  // A dense kerb row, 1.5 m apart, and a 3 m-grid lot: many cars share the boundary distance.
  ['kerb row', Array.from({ length: 150 }, (_, i) => slot(`k${i}`, i * 1.5, 4)), [50.3, 0], [54.3, 0]],
  ['parking lot', cluster('p', 0, 0, 150), [0.3, 0.2], [2.4, 2.5]],
] as const)('%s: jitter of up to 4 m never flips cars between physical and visual', async (_name, cars, [x, z], [x2, z2]) => {
  const { physics, at } = world([...cars], 40);
  await at(x, z);
  await at(x2, z2);
  const churn = () => physics.spawnVehicle.mock.calls.length + physics.removeVehicle.mock.calls.length;
  const settled = churn();
  for (let i = 0; i < 8; i++) { await at(x, z); await at(x2, z2); }
  expect(churn()).toBe(settled);
  // Real travel still re-ranks: bodies follow the player.
  await at(x + 40, 0);
  expect(churn()).toBeGreaterThan(settled);
});

test.each([false, true])('trucks are exempt, claimed slots neither spawn nor rest locally, rolling=%s keeps its body', async rolling => {
  const cars = [slot('near', 0, 5), slot('mid', 0, 10), slot('ahead', 0, -20), slot('claimed', 0, 2), slot('truck', 0, 3, 'truck')];
  const { physics, bodies, physical, stream, at } = world(cars, 2);
  stream.setShared(new Set(['claimed']), new Set());
  await at(0, 0);
  expect(physical()).toEqual(new Set(['truck', 'near', 'mid']));
  expect(stream.restingPose(cars[3])).toBeNull();
  const half = (0.3 - Math.PI / 2) / 2;
  expect(stream.restingPose(cars[2])).toEqual({ pos: { x: 0, y: 2.75, z: -20 }, rot: { x: 0, y: Math.sin(half), z: 0, w: Math.cos(half) } });
  if (rolling) { bodies.get('mid')!.speed = 2; physics.latest.vehicles = [...bodies.values()]; }
  // At z = -10 `mid` is the farthest body; only a resting one gives way to the nearer `ahead`.
  await at(0, -10);
  expect(physical()).toEqual(new Set(['truck', 'ahead', rolling ? 'mid' : 'near']));
  expect(physics.spawnVehicle).not.toHaveBeenCalledWith(expect.anything(), expect.anything(), expect.objectContaining({ id: 'claimed' }));
});

test('a saturated cap re-ranks every 250 ms; an unsaturated one once per second', async () => {
  const cars = [slot('a', 0, 5), slot('b', 0, 10), slot('c', 0, 15)];
  const capped = world(cars, 2);
  capped.stream.update(0, 0, null, 0); await settle();
  expect(capped.physical()).toEqual(new Set(['a', 'b']));
  capped.stream.update(0, 20, null, 250); await settle();
  expect(capped.physical()).toEqual(new Set(['b', 'c']));
  const open = world(cars.slice(0, 2), 5);
  open.stream.update(0, 0, null, 0); await settle();
  open.manifest.vehicles!.push(cars[2]);
  open.stream.update(0, 0, null, 250); await settle();
  expect(open.physical()).toEqual(new Set(['a', 'b']));
  open.stream.update(0, 0, null, 1000); await settle();
  expect(open.physical()).toEqual(new Set(['a', 'b', 'c']));
});
