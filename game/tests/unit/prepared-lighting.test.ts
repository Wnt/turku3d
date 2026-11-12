import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { prepareLighting } from '../../src/render/lights/preparedLighting';
import { LampPools, poolLamp, sightBlocked } from '../../src/render/lights/pools';
import { StreetLights } from '../../src/render/lights';
import type { PreparedScene } from '../../src/world/preparedScene';

const scene = () => ({ id: 'office', release: 'one', fixtures: [{ id: 'mapped-pole', position: [0, 3, 0], yaw: .4 }],
  emitters: [{ id: 'mapped-head', fixtureId: 'mapped-pole', position: [1, 11, 0], category: 'street', color: [1, .78, .52], intensity: 90.123, range: 28.456 }],
  lightOccluders: [{ id: 'source-building', positions: [[4, -4], [8, -4], [8, 4], [4, 4]], minY: 3, maxY: 7 }],
} as PreparedScene);

test('prepared lighting retains full photometry and source identity without constructing posts', () => {
  const result = prepareLighting(scene()), lamp = result.lamps[0], pool = poolLamp(lamp)!;
  expect(lamp.key).toBe('mapped-head'); expect(lamp.post).toBe(false);
  expect(lamp.photometry).toEqual({ color: [1, .78, .52], intensity: 90.123, range: 28.456, base: 3 });
  expect(pool).toMatchObject({ x: 1, y: 11, z: 0, r: 1, g: .78, b: .52, intensity: 90.123, range: 28.456 });
  const slots = [new THREE.PointLight()], pools = new LampPools(slots);
  pools.setLamps([pool]);
  for (let t = 0; t <= 1000; t += 100) pools.update(0, 0, 0, 0, 1, t);
  expect(slots[0].intensity).toBeCloseTo(90.123, 10); expect(slots[0].distance).toBe(28.456);
  expect(slots[0].position.toArray()).toEqual([1, 11, 0]); expect(slots[0].color.g).toBe(.78);
  pools.update(0, 0, 0, 0, 0, 1100); expect(slots[0].intensity).toBe(0);
});

test('source building polygon height blocks light only where the ray intersects its volume', () => {
  const footprint = prepareLighting(scene()).footprint!;
  expect(footprint.covers(5, 1, 5)).toBe(true); expect(footprint.covers(5, 1, 9)).toBe(false);
  expect(footprint.covers(3, 1, 5)).toBe(false);
  expect(sightBlocked([footprint], 0, 0, 12, 0, 5, 5)).toBe(true);
  expect(sightBlocked([footprint], 0, 0, 12, 0, 10, 10)).toBe(false);
  expect(footprint.revision).toBe('one');
});

test('normal light attachment installs only the prepared records and replaces the previous scene', () => {
  const lights = new StreetLights(new THREE.Scene(), 'mobile');
  lights.attach({ lighting: prepareLighting(scene()) });
  expect(lights.snapshot()).toMatchObject({ lamps: 1, posts: 0, tiles: 1 });
  expect(lights.originalLamp('mapped-head')?.lamp.photometry?.range).toBe(28.456);
  const empty = scene(); empty.id = 'next'; empty.emitters = [];
  lights.attach({ lighting: prepareLighting(empty) });
  expect(lights.snapshot()).toMatchObject({ lamps: 0, posts: 0, tiles: 1 });
  expect(lights.originalLamp('mapped-head')).toBeNull(); lights.dispose();
});
