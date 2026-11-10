import { expect, test, vi } from 'vitest';
import { ParkedVehicles, slotRotation } from '../../src/world/parkedVehicles';
import { draftManifest, type ParkedVehicle } from '../../src/world/manifest';
import type { PhysicsClient } from '../../src/physics/client';

const car: ParkedVehicle = { object_id: 'draft:t0_0_0:slot', model: 'sedan', color: 'blue', x: 30, y: 5, z: 30, yaw: .7,
  width: 1.85, length: 4.55, height: 1.48, clear_front_m: 10, clear_rear_m: 10 };
const settle = () => new Promise(r => setTimeout(r, 0));

test('draft cars wait for collision, spawn once, retain borrowed identity and unload untouched cars', async () => {
  const manifest = draftManifest('http://localhost/'); manifest.vehicles = [car, { ...car, object_id: 'other', x: 40 }];
  const physics = { loaded: new Map(), latest: { vehicles: [] }, spawnVehicle: vi.fn(), removeVehicle: vi.fn() } as unknown as PhysicsClient;
  const visuals = { stream: vi.fn(async () => {}), has: () => true }, seated = { addTruck: vi.fn(async () => {}), retainTrucks: vi.fn() };
  const stream = new ParkedVehicles(manifest, physics, visuals, seated);
  stream.update(30, 30, null, 0); await settle();
  expect(physics.spawnVehicle).not.toHaveBeenCalled();
  physics.loaded.set('c_0_0', {} as never);
  stream.update(30, 30, null, 1000); await settle();
  expect(physics.spawnVehicle).toHaveBeenCalledTimes(2);
  expect(physics.spawnVehicle).toHaveBeenCalledWith({ x: 30, y: 5.75, z: 30 }, .7 - Math.PI / 2,
    expect.objectContaining({ id: car.object_id, kind: 'car', parked: true }));
  stream.update(30, 30, car.object_id, 2000); await settle();
  manifest.vehicles = [];
  stream.update(1000, 1000, null, 3000); await settle();
  expect(physics.removeVehicle).toHaveBeenCalledExactlyOnceWith('other');
  expect(manifest.vehicles).toEqual([car]);
  stream.update(30, 30, null, 4000); await settle();
  expect(physics.spawnVehicle).toHaveBeenCalledTimes(2);
});

test('rare parked truck uses the built-in truck appearance and physics without a passenger-car chassis override', async () => {
  const manifest = draftManifest('http://localhost/'); manifest.vehicles = [{ ...car, model: 'truck', width: 2.5, length: 5.6 }];
  const physics = { loaded: new Map([['c_0_0', {}]]), latest: { vehicles: [] }, spawnVehicle: vi.fn(), removeVehicle: vi.fn() } as unknown as PhysicsClient;
  const seated = { addTruck: vi.fn(async () => {}), retainTrucks: vi.fn() };
  const stream = new ParkedVehicles(manifest, physics, { stream: async () => {}, has: () => true }, seated);
  stream.update(30, 30, null, 0); await settle();
  expect(seated.addTruck).toHaveBeenCalledExactlyOnceWith(car.object_id);
  expect(physics.spawnVehicle).not.toHaveBeenCalled();
  stream.update(30, 30, null, 1000); await settle();
  expect(physics.spawnVehicle).toHaveBeenCalledWith({ x: 30, y: 6.0, z: 30 }, .7 - Math.PI / 2,
    { id: car.object_id, kind: 'truck', parked: true, rot: slotRotation(car) });
  manifest.vehicles = [];
  stream.update(1000, 1000, null, 2000); await settle();
  expect(seated.retainTrucks).toHaveBeenLastCalledWith(new Set());
});

test('global claimed slots never respawn locally, and expiry releases the source slot', async () => {
  const manifest = draftManifest('http://localhost/'); manifest.vehicles = [car];
  const physics = { loaded: new Set(['c_0_0']), latest: { vehicles: [] }, spawnVehicle: vi.fn(), removeVehicle: vi.fn() };
  const visuals = { stream: vi.fn(async () => {}), has: () => true }, seated = { addTruck: vi.fn(), retainTrucks: vi.fn() };
  const stream = new ParkedVehicles(manifest, physics as never, visuals, seated);
  stream.setShared(new Set([car.object_id]), new Set());
  stream.update(30, 30, null, 0); await settle();
  expect(physics.spawnVehicle).not.toHaveBeenCalled();
  expect(physics.removeVehicle).toHaveBeenCalledWith(car.object_id);
  stream.setShared(new Set(), new Set());
  stream.update(30, 30, null, 2000); await settle();
  expect(physics.spawnVehicle).toHaveBeenCalledExactlyOnceWith(expect.anything(), expect.anything(), expect.objectContaining({ id: car.object_id }));
});

test('cars already drawn get bodies near the player while a later appearance load waits in the GPU queue', async () => {
  // Regression: the body sync awaited every fleet visual load, which waits behind all streamed tiles' GPU preparation;
  // while the player moved, the cars they reached got no body, so they walked through them and F found none to enter.
  const ahead = { ...car, object_id: 'ahead', x: 230 };
  const manifest = draftManifest('http://localhost/'); manifest.vehicles = [car, ahead];
  const physics = { loaded: { has: () => true }, latest: { vehicles: [] }, spawnVehicle: vi.fn(), removeVehicle: vi.fn() } as unknown as PhysicsClient;
  const drawn = new Set<string>();
  let release: () => void = () => {};
  const visuals = {
    stream: vi.fn(() => new Promise<void>(resolve => {
      const ids = (manifest.vehicles ?? []).map(v => v.object_id);
      release = () => { for (const id of ids) drawn.add(id); resolve(); };
    })),
    has: (id: string) => drawn.has(id),
  };
  const stream = new ParkedVehicles(manifest, physics, visuals, { addTruck: vi.fn(async () => {}), retainTrucks: vi.fn() });
  stream.update(30, 30, null, 0); await settle();
  // No body before its car is drawn: nothing collides unseen.
  expect(physics.spawnVehicle).not.toHaveBeenCalled();
  release(); await settle();
  stream.update(30, 30, null, 1000); await settle();
  expect(physics.spawnVehicle).toHaveBeenCalledExactlyOnceWith(expect.anything(), expect.anything(), expect.objectContaining({ id: car.object_id }));
  // A tile streams in another car; its appearance load does not finish (a backed-up preparation queue)...
  manifest.vehicles = [car, ahead, { ...car, object_id: 'streamed', x: 234 }];
  stream.update(30, 30, null, 2000); await settle();
  expect(visuals.stream).toHaveBeenCalledTimes(2);
  // ...while the player walks on to the drawn car ahead: it gets its body, the undrawn one does not.
  stream.update(230, 30, null, 3000); await settle();
  expect(physics.spawnVehicle).toHaveBeenLastCalledWith(expect.anything(), expect.anything(), expect.objectContaining({ id: 'ahead', parked: true }));
  expect(physics.spawnVehicle).not.toHaveBeenCalledWith(expect.anything(), expect.anything(), expect.objectContaining({ id: 'streamed' }));
  release(); await settle();
  stream.update(230, 30, null, 4000); await settle();
  expect(physics.spawnVehicle).toHaveBeenLastCalledWith(expect.anything(), expect.anything(), expect.objectContaining({ id: 'streamed' }));
});
