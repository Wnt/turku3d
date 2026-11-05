import { expect, test } from 'vitest';
import { findInitialGround, safeSurface, streetCandidates, supportedSpot } from '../../src/world/startGround';
import { MAT } from '../../src/shared/col/materials';
import type { RayHit } from '../../src/physics/protocol';
import type { RoadGraphJson } from '../../src/gameplay/roadGraph';

const roads: RoadGraphJson[] = [{ format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz',
  bbox: [0, -10, 30, 10], nodes: [0, 0, 30, 0], edges: [[0, 1, 3, 30]] }];
const ground = (x: number, z: number, material: number = MAT.road, y = 2): RayHit => ({
  toi: 500 - y, point: { x, y, z }, normal: { x: 0, y: 1, z: 0 }, material, tile: 'c_0_0',
});

test('parking candidates follow the nearest CAR street instead of arbitrary nearby roof points', () => {
  expect(streetCandidates({ x: 10, z: 2 }, roads)[0]).toMatchObject({ x: 10, z: 0, yaw: -0 });
  expect(streetCandidates({ x: 10, z: 2 }, [{ ...roads[0], edges: [[0, 1, 1, 30]] }])).toEqual([]);
});

test('a roof at the stored/default point moves the car to the nearest free supported street', async () => {
  const probe = async (x: number, z: number) => ground(x, z, x >= 6 && x <= 14 ? MAT.roof : MAT.road,
    x >= 6 && x <= 14 ? 15 : 2);
  const result = await findInitialGround({ x: 10, z: 2 }, roads, probe, () => true);
  expect(result?.car).toMatchObject({ x: 3, z: 0, y: 2 });
  expect(result?.spawn.y).toBe(2);
  expect(Math.hypot(result!.spawn.x - result!.car.x, result!.spawn.z - result!.car.z)).toBeGreaterThan(3);
});

test('the whole chassis and player footprints need supported surfaces', async () => {
  expect(await supportedSpot({ x: 10, z: 0, yaw: 0 }, async (x, z) =>
    ground(x, z, x > 12 ? MAT.roof : MAT.road), true)).toBeNull();
  expect(await supportedSpot({ x: 10, z: 0, yaw: 0 }, async (x, z) =>
    ground(x, z, MAT.road, x > 12 ? 3 : 2), true)).toBeNull();
  expect(await findInitialGround({ x: 10, z: 0 }, roads, async (x, z) => ground(x, z), () => false)).toBeNull();
  for (const material of [MAT.roof, MAT.building, MAT.water, MAT.unknown]) {
    expect(safeSurface(ground(0, 0, material))).toBe(false);
    expect(safeSurface(ground(0, 0, material), true)).toBe(false);
  }
});

test('streets under roofs and decks fall back to the nearest open walkable spot once the start collision is complete', async () => {
  // Every street point is under a roof; open paving starts 20 m east of the requested point, a road 30 m east.
  const probe = async (x: number, z: number) => x < 20 ? ground(x, z, MAT.roof, 40) : ground(x, z, x >= 30 ? MAT.road : MAT.paving_slab);
  expect(await findInitialGround({ x: 10, z: 0 }, roads.map(r => ({ ...r, nodes: [0, 0, 18, 0] })), probe, () => true)).toBeNull();
  const result = await findInitialGround({ x: 10, z: 0 }, roads.map(r => ({ ...r, nodes: [0, 0, 18, 0] })), probe, () => true, true);
  expect(result?.spawn).toMatchObject({ x: 22, z: 0, y: 2 }); // The 0.4 m footprint clears the roof edge.
  expect(result!.car.x).toBeGreaterThanOrEqual(20 + 1.15);
  expect(Math.hypot(result!.spawn.x - result!.car.x, result!.spawn.z - result!.car.z)).toBeGreaterThanOrEqual(4);
});

test('with no street surface in reach the car parks on flat walkable ground', async () => {
  const probe = async (x: number, z: number) => ground(x, z, MAT.grass);
  const result = await findInitialGround({ x: 0, z: 0 }, [], probe, () => true, true);
  expect(result?.spawn).toMatchObject({ x: 0, z: 0 });
  expect(Math.hypot(result!.car.x, result!.car.z)).toBeGreaterThanOrEqual(4);
  expect(await findInitialGround({ x: 0, z: 0 }, [], async (x, z) => ground(x, z, MAT.roof), () => true, true)).toBeNull();
});

test('a square start keeps the player on the square and parks the car on the street beyond it', async () => {
  // The street runs 40 m south of the requested point across open paving.
  const far: RoadGraphJson[] = [{ ...roads[0], nodes: [-30, 40, 30, 40] }];
  const probe = async (x: number, z: number) => ground(x, z, z >= 37 ? MAT.road : MAT.paving_slab);
  expect(await findInitialGround({ x: 0, z: 0 }, far, probe, () => true)).toBeNull(); // waits for the start collision
  const result = await findInitialGround({ x: 0, z: 0 }, far, probe, () => true, true);
  expect(result?.spawn).toMatchObject({ x: 0, z: 0 });
  expect(result?.car).toMatchObject({ z: 40 });
});
