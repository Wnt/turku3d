import { expect, test, vi } from 'vitest';
import { ENTER_REACH, footprintDistance, yawRotation } from '../../src/vehicle/reach';
import { PhysicsClient } from '../../src/physics/client';
import { VEHICLES } from '../../src/physics/constants';

// A transit bus (11.47 x 2.93 m) at (10, -4), turned 0.7 rad; chassis +X forward, +Z to its right.
const bus = { pos: { x: 10, z: -4 }, rot: yawRotation(0.7), half: { x: 5.73, z: 1.46 } };
const ahead = { x: Math.cos(0.7), z: -Math.sin(0.7) }, right = { x: Math.sin(0.7), z: Math.cos(0.7) };
const at = (along: number, across: number) => ({ x: bus.pos.x + ahead.x * along + right.x * across, z: bus.pos.z + ahead.z * along + right.z * across });
const toBus = (p: { x: number; z: number }) => footprintDistance(p, bus.pos, bus.rot, bus.half);

test('reach is measured from the footprint: a bus is entered at its front door, not only from mid-side', () => {
  expect(toBus(at(0, 0))).toBe(0);
  expect(toBus(at(5, 1))).toBe(0);
  // 0.8 m ahead of the bumper, and 0.6 m out from the front door: centre distances 6.5 and 5.1 m (beyond the old 4 m).
  expect(toBus(at(5.73 + 0.8, 0))).toBeCloseTo(0.8, 6);
  expect(toBus(at(4.5, 1.46 + 0.6))).toBeCloseTo(0.6, 6);
  expect(Math.hypot(4.5, 2.06)).toBeGreaterThan(4);
  // Off a corner: the diagonal gap.
  expect(toBus(at(-5.73 - 1.2, -1.46 - 1.6))).toBeCloseTo(2, 6);
  expect(toBus(at(5.73 + ENTER_REACH + 0.1, 0))).toBeGreaterThan(ENTER_REACH);
});

test('at a bus front door with a car parked alongside, the bus is the nearer vehicle', () => {
  // A car (4.55 x 1.85 m) parked parallel to the bus, 1.8 m off its right side.
  const car = { pos: at(3, 4.2), rot: bus.rot, half: { x: 2.28, z: 0.93 } };
  const door = at(4.2, 1.46 + 0.7);
  const toCar = footprintDistance(door, car.pos, car.rot, car.half);
  expect(toBus(door)).toBeLessThan(toCar);
  // The centre rule picked the car instead.
  expect(Math.hypot(door.x - car.pos.x, door.z - car.pos.z)).toBeLessThan(Math.hypot(door.x - bus.pos.x, door.z - bus.pos.z));
});

test('a body on its nose counts as a disc of its smaller half extent', () => {
  const nose = { x: 0, y: 0, z: Math.sin(Math.PI / 4), w: Math.cos(Math.PI / 4) };
  expect(footprintDistance({ x: 3, z: 0 }, { x: 0, z: 0 }, nose, { x: 2.2, z: 0.9 })).toBeCloseTo(2.1, 6);
});

test('the physics client keeps each spawned body\'s half extents for the reach test', async () => {
  class FakeWorker { onmessage?: unknown; onerror?: unknown; postMessage = vi.fn(); }
  vi.stubGlobal('Worker', FakeWorker);
  try {
    const client = new PhysicsClient(), p = { x: 0, y: 0, z: 0 };
    client.spawnVehicle(p, 0, { id: 'bus', halfExt: { x: 5.73, y: .8, z: 1.46 } });
    client.spawnVehicle(p, 0, { id: 'tuned', tuning: { ...VEHICLES.car, halfExt: { x: 2.4, y: .4, z: .95 } } });
    client.spawnVehicle(p, 0, { id: 'scooter', kind: 'scooter' });
    expect(client.vehicleHalfExt('bus')).toEqual({ x: 5.73, y: .8, z: 1.46 });
    expect(client.vehicleHalfExt('tuned')).toEqual({ x: 2.4, y: .4, z: .95 });
    expect(client.vehicleHalfExt('scooter')).toEqual(VEHICLES.scooter.halfExt);
    client.removeVehicle('bus');
    expect(client.vehicleHalfExt('bus', 'truck')).toEqual(VEHICLES.truck.halfExt);
  } finally { vi.unstubAllGlobals(); }
});
