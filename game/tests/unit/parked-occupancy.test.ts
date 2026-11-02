import { expect, test, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { LOW_PRIORITY, OCCUPANCY, occupancy, parkedNight, slotShown } from '../../src/world/parkedOccupancy';
import { CYCLE_EPOCH, DAY_MS } from '../../src/core/dayCycle';
import { ParkedVehicles } from '../../src/world/parkedVehicles';
import { draftManifest, type ParkedVehicle } from '../../src/world/manifest';
import type { V3 } from '../../src/core/origin';
import type { Quat, VehicleSpec, VehicleState } from '../../src/physics/protocol';

const settle = () => new Promise(r => setTimeout(r, 0));
const slot = (object_id: string, zone: string, occ: number, x = 0, priority = 1): ParkedVehicle => ({ object_id, model: 'sedan',
  color: 'blue', x, y: 2, z: 0, yaw: 0, width: 1.85, length: 4.55, height: 1.48, clear_front_m: 1, clear_rear_m: 1, zone, occ, priority });

test('each class peaks at the occupancy the generator places cars up to', () => {
  const source = readFileSync(new URL('../../../pipeline/turku3d/coarse/parked.py', import.meta.url), 'utf8');
  const peak = Object.fromEntries([...source.match(/PEAK = \{([^}]*)\}/)![1].matchAll(/"([\w-]+)": ([\d.]+)/g)].map(([, k, v]) => [k, +v]));
  expect(Object.keys(peak).sort()).toEqual(Object.keys(OCCUPANCY).sort());
  for (const [zone, { day, night }] of Object.entries(OCCUPANCY)) expect(Math.max(day, night)).toBe(peak[zone]);
  // The centre empties at night, residential streets fill up.
  expect(occupancy('centre-kerb', 1)).toBeLessThan(occupancy('centre-kerb', 0));
  expect(occupancy('residential-kerb', 1)).toBeGreaterThan(occupancy('residential-kerb', 0));
});

test('slot visibility is a pure function of the slot draw and the night level', () => {
  // Draws as the generator leaves them: uniform below the class peak.
  const draws = Array.from({ length: 2000 }, (_, i) => ((i * 0.6180339887) % 1));
  for (const zone of Object.keys(OCCUPANCY)) {
    const peak = Math.max(OCCUPANCY[zone].day, OCCUPANCY[zone].night);
    const cars = draws.filter(d => d < peak).map((occ, i) => slot(`${zone}${i}`, zone, occ));
    const shown = (night: number) => cars.filter(c => slotShown(c, night));
    const day = shown(0), dark = shown(1);
    expect(day.length / cars.length).toBeCloseTo(OCCUPANCY[zone].day / peak, 1);
    expect(dark.length / cars.length).toBeCloseTo(OCCUPANCY[zone].night / peak, 1);
    // Fewer cars only ever means a subset: a car never swaps places with another one.
    const [few, many] = day.length < dark.length ? [day, dark] : [dark, day];
    expect(few.every(c => many.includes(c))).toBe(true);
    // Two clients with the same clock agree on every slot.
    for (const night of [0, 0.3, 0.71, 1]) expect(cars.map(c => slotShown({ ...c }, night))).toEqual(cars.map(c => slotShown(c, night)));
  }
  expect(slotShown(slot('low', 'residential-kerb', 0.7, 0, 0), 1)).toBe(0.7 < 0.92 * LOW_PRIORITY);
  expect(slotShown(slot('low', 'residential-kerb', 0.79, 0, 0), 1)).toBe(false);
  // Drafts from before occupancy draws keep every car.
  expect(slotShown({ zone: undefined, occ: undefined }, 1)).toBe(true);
});

test('an empty slot draws no car and gets no body; a resting body leaves when its slot empties', async () => {
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
  // Centre kerb: 0.9 by day, 0.62 at night. Residential kerb: 0.74 by day, 0.92 at night.
  const office = slot('office', 'centre-kerb', 0.8, 0), home = slot('home', 'residential-kerb', 0.8, 10);
  const always = slot('always', 'centre-kerb', 0.1, 20);
  const manifest = draftManifest('http://localhost/'); manifest.vehicles = [office, home, always];
  const stream = new ParkedVehicles(manifest, physics as never, { load: async () => {} },
    { addTruck: vi.fn(async () => {}), retainTrucks: vi.fn() }, 60);
  let now = 0;
  const at = async (night: number) => { stream.setNight(night); stream.update(0, 0, null, now += 1000); await settle(); };
  await at(0);
  expect(new Set(bodies.keys())).toEqual(new Set(['office', 'always']));
  expect(stream.restingPose(home)).toBeNull();
  // Dusk: the office car stays while it still rolls, then goes; the home car arrives.
  bodies.get('office')!.speed = 2;
  await at(1);
  expect(new Set(bodies.keys())).toEqual(new Set(['office', 'always', 'home']));
  bodies.get('office')!.speed = 0;
  await at(1);
  expect(new Set(bodies.keys())).toEqual(new Set(['always', 'home']));
  expect(stream.restingPose(office)).toBeNull();
  expect(stream.restingPose(home)).not.toBeNull();
  // Driven or claimed cars are never emptied.
  stream.update(0, 0, 'home', now += 1000); await settle();
  await at(0);
  expect(bodies.has('home')).toBe(true);
});

test('the night level comes from the shared cycle alone: day, then dusk, full night, dawn', () => {
  const at = (minutes: number) => parkedNight(CYCLE_EPOCH + minutes * 60_000);
  expect(at(10)).toBe(0);
  expect(at(26)).toBe(1);
  expect(at(10 + DAY_MS / 60_000)).toBe(0);
  // Through dusk the level only rises, so slots empty or fill one at a time and never flicker.
  const dusk = Array.from({ length: 41 }, (_, i) => at(20 + i * 0.1));
  expect(dusk.every((v, i) => i === 0 || v >= dusk[i - 1])).toBe(true);
  expect(dusk[0]).toBeLessThan(0.05);
  expect(dusk[40]).toBeGreaterThan(0.95);
});

test('single player\'s view clock (?time=, the developer time panel) sets the night level; without one the shared cycle does', () => {
  const day = CYCLE_EPOCH + 10 * 60_000;
  expect(parkedNight(day)).toBe(0);
  expect(parkedNight(day, () => 22)).toBe(1);
  expect(parkedNight(day, () => 13)).toBe(0);
  expect(parkedNight(day, () => 20.5)).toBeGreaterThan(0.5);
  expect(parkedNight(CYCLE_EPOCH + 26 * 60_000, () => 13)).toBe(0);
});
