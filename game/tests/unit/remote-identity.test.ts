import { expect, test } from 'vitest';
import { vehicleIdentity } from '../../src/net/identity';
import type { ParkedVehicle } from '../../src/world/manifest';

const parked = [{ object_id: 'parked-1', model: 'hatchback-sports', color: 'red' }] as ParkedVehicle[];

test('vehicle identity follows the driven model and paint', () => {
  expect(vehicleIdentity('foot', null, parked)).toBeNull();
  expect(vehicleIdentity('scooter', 'scooter', parked)).toBe('scooter');
  expect(vehicleIdentity('truck', 'truck', parked)).toBe('truck');
  expect(vehicleIdentity('car', 'player', parked)).toBe('player');
  expect(vehicleIdentity('car', 'parked-1', parked)).toBe('hatchback-sports/red');
  expect(() => vehicleIdentity('car', 'missing', parked)).toThrow('missing driven vehicle identity');
});
