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
  const result = await findInitialGround({ x: 10, z: 0 }, roads.map(r => ({ ...r, nodes: [0, 0, 18, 0] })), probe, () => true, { open: true });
  expect(result?.spawn).toMatchObject({ x: 22, z: 0, y: 2 }); // The 0.4 m footprint clears the roof edge.
  expect(result!.car.x).toBeGreaterThanOrEqual(20 + 1.15);
  expect(Math.hypot(result!.spawn.x - result!.car.x, result!.spawn.z - result!.car.z)).toBeGreaterThanOrEqual(4);
});

test('with no street surface in reach the car parks on flat walkable ground', async () => {
  const probe = async (x: number, z: number) => ground(x, z, MAT.grass);
  const result = await findInitialGround({ x: 0, z: 0 }, [], probe, () => true, { open: true });
  expect(result?.spawn).toMatchObject({ x: 0, z: 0 });
  expect(Math.hypot(result!.car.x, result!.car.z)).toBeGreaterThanOrEqual(4);
  expect(await findInitialGround({ x: 0, z: 0 }, [], async (x, z) => ground(x, z, MAT.roof), () => true, { open: true })).toBeNull();
});

test('a square start keeps the player on the square and parks the car on the street beyond it', async () => {
  // The street runs 40 m south of the requested point across open paving.
  const far: RoadGraphJson[] = [{ ...roads[0], nodes: [-30, 40, 30, 40] }];
  const probe = async (x: number, z: number) => ground(x, z, z >= 37 ? MAT.road : MAT.paving_slab);
  expect(await findInitialGround({ x: 0, z: 0 }, far, probe, () => true)).toBeNull(); // waits for the start collision
  const result = await findInitialGround({ x: 0, z: 0 }, far, probe, () => true, { open: true });
  expect(result?.spawn).toMatchObject({ x: 0, z: 0 });
  expect(result?.car).toMatchObject({ z: 40 });
});

test('the car never parks over furniture or a parked car, and the first chase camera is not inside it', async () => {
  const { cameraInCar, spotClear } = await import('../../src/world/startGround');
  // A bench across the street where the nearest candidate (x 10) would park.
  const bench = { minX: 9, maxX: 11, minZ: -0.6, maxZ: 0.6 };
  const result = await findInitialGround({ x: 10, z: 2 }, roads, async (x, z) => ground(x, z), () => true, { obstacles: [bench] });
  expect(result).not.toBeNull();
  expect(spotClear([bench], result!.car, true)).toBe(true);
  expect(Math.abs(result!.car.x - 10)).toBeGreaterThan(3);
  // A spot 3.2 m on the -z side of a car on the x axis: looking away from the car (-z) puts the camera behind the
  // player, in the car; looking at the car (+z) puts it out on the open side.
  const car = { x: 0, z: 0, yaw: 0 };
  expect(cameraInCar({ x: 0, z: -3.2 }, car, { dx: 0, dz: -1 })).toBe(true);
  expect(cameraInCar({ x: 0, z: -3.2 }, car, { dx: 0, dz: 1 })).toBe(false);
  const viewed = await findInitialGround({ x: 10, z: 2 }, roads, async (x, z) => ground(x, z), () => true, { view: { dx: 0, dz: -1 } });
  expect(cameraInCar(viewed!.spawn, viewed!.car, { dx: 0, dz: -1 })).toBe(false);
});

test('tunnels and building passages are never street starts', () => {
  const tunnel: RoadGraphJson = { ...roads[0], revision: 2, edgeIds: ['w1'], edgeAttrColumns: ['way', 'flags'], edgeAttrs: [[1, 8]] };
  expect(streetCandidates({ x: 10, z: 2 }, [tunnel])).toEqual([]);
  expect(streetCandidates({ x: 10, z: 2 }, [{ ...tunnel, edgeAttrs: [[1, 1]] }]).length).toBeGreaterThan(0);
});

test('a street under a deck does not park the car on the deck above it (Tapiola, Merituulentie)', async () => {
  // The street is 2 m up per its heights; a deck at 9 m covers x 0..20, open street beyond.
  const covered: RoadGraphJson = { ...roads[0], edgeHeights: [[2, 2]] };
  const probe = async (x: number, z: number) => ground(x, z, MAT.road, x <= 20 ? 9 : 2);
  const result = await findInitialGround({ x: 10, z: 2 }, [covered], probe, () => true);
  expect(result?.car.x).toBeGreaterThan(20);
  expect(result?.car.y).toBe(2);
  // Without heights (v1 graphs) the old behaviour stands.
  expect((await findInitialGround({ x: 10, z: 2 }, roads, probe, () => true))?.car.y).toBe(9);
});

// A through street along z = 0 leaving the loaded area at both ends (seam nodes), and a courtyard loop south of it
// behind one gate edge (Läntinen Rantakatu 15: the office courtyard behind its fence).
const STREET = 2 | (2 << 3), SERVICE = 2 | (1 << 3);
const town: RoadGraphJson = { format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', bbox: [-50, -40, 50, 10],
  nodes: [-50, 0, 0, 0, 50, 0, 0, -10, 10, -10, 10, -25, 0, -25],
  edges: [[0, 1, STREET, 50], [1, 2, STREET, 50], [1, 3, SERVICE, 10], [3, 4, SERVICE, 10], [4, 5, SERVICE, 15], [5, 6, SERVICE, 10], [6, 3, SERVICE, 15]],
  nodeFlags: [32, 0, 32, 0, 0, 0, 0] };

test('courtyards and dead ends behind one connection are pockets; a street to the edge of the loaded area is not', async () => {
  const { pocketEdges } = await import('../../src/world/startGround');
  const { parseRoadGraph } = await import('../../src/gameplay/roadGraph');
  expect([...pocketEdges(parseRoadGraph(town))]).toEqual([0, 0, 1, 1, 1, 1, 1]);
  // A stub that leaves the loaded area is a street, not a dead end.
  expect([...pocketEdges(parseRoadGraph({ ...town, nodeFlags: [32, 0, 32, 32, 0, 0, 0] }))]).toEqual([0, 0, 0, 0, 0, 0, 0]);
  // The courtyard is nearer, but the through street comes first.
  const candidates = streetCandidates({ x: 5, z: -12 }, [town]);
  expect(candidates[0]).toMatchObject({ z: 0, pocket: false });
  expect(candidates.find(c => c.pocket)).toBeDefined();
});

test('the start car faces open road along its street and the player faces open street, not a wall', async () => {
  const { openView } = await import('../../src/world/startGround');
  // A fence across the street at x = 15 and the facade of a building along z = -6.
  const ray = async (o: { x: number; y: number; z: number }, d: { x: number; y: number; z: number }, max: number): Promise<RayHit | null> => {
    const hits: RayHit[] = [];
    if (d.x > 1e-6 && o.x < 15) { const t = (15 - o.x) / d.x, z = o.z + d.z * t; if (t <= max && Math.abs(z) < 8) hits.push({ ...ground(15, z, MAT.fence), toi: t, normal: { x: -1, y: 0, z: 0 } }); }
    if (d.z < -1e-6 && o.z > -6) { const t = (-6 - o.z) / d.z; if (t <= max) hits.push({ ...ground(o.x + d.x * t, -6, MAT.building), toi: t, normal: { x: 0, y: 0, z: 1 } }); }
    return hits.sort((a, b) => a.toi - b.toi)[0] ?? null;
  };
  const north = { dx: 0, dz: -1 };
  const result = await findInitialGround({ x: 5, z: -12 }, [town], async (x, z) => ground(x, z), () => true, { view: north, ray });
  expect(result!.car.z).toBeCloseTo(0);
  expect(Math.cos(result!.car.yaw)).toBeLessThan(-0.9); // facing -x, away from the fence
  // The player looks along the open street (-x), not into the facade 3 m ahead.
  expect(-Math.sin(result!.spawn.yaw)).toBeLessThan(-0.9);
  // An open requested view is kept.
  expect(await openView({ x: 0, y: 2, z: 3 }, Math.PI, ray)).toBe(Math.PI);
  // Without rays the car keeps the street's direction and the requested view stands.
  const plain = await findInitialGround({ x: 5, z: 3 }, [town], async (x, z) => ground(x, z), () => true, { view: north });
  expect(plain!.car.z).toBeCloseTo(0);
  expect(plain!.spawn.yaw).toBeCloseTo(0);
});

test('a pole ahead is not a wall: the car keeps its way when a lane beside the pole is open', async () => {
  const pole = async (o: { x: number; y: number; z: number }, d: { x: number; y: number; z: number }, max: number): Promise<RayHit | null> => {
    if (d.x <= 1e-6 || o.x >= 12) return null;
    const t = (12 - o.x) / d.x, z = o.z + d.z * t;
    return t <= max && Math.abs(z) < 0.15 ? { ...ground(12, z, MAT.pole), toi: t, normal: { x: -1, y: 0, z: 0 } } : null;
  };
  const result = await findInitialGround({ x: 5, z: -12 }, [town], async (x, z) => ground(x, z), () => true, { view: { dx: 0, dz: -1 }, ray: pole });
  expect(Math.cos(result!.car.yaw)).toBeGreaterThan(0.9);
});

test('a through street whose collision is still loading is waited for, not traded for a courtyard', async () => {
  // Only the courtyard's collision has streamed in (z < -5); the street at z = 0 is not covered yet.
  const covers = (_x: number, z: number) => z < -5;
  expect(await findInitialGround({ x: 5, z: -12 }, [town], async (x, z) => ground(x, z), covers)).toBeNull();
  // Once the start collision is complete, an uncovered street is out of reach and the courtyard is the start.
  const result = await findInitialGround({ x: 5, z: -12 }, [town], async (x, z) => ground(x, z), covers, { open: true });
  expect(result!.car.z).toBeLessThan(-5);
});

test('players of one room get start cars apart from each other, in the start search order', async () => {
  const { startCars, START_CAR_GAP_M } = await import('../../src/world/startGround');
  const spots = await startCars({ x: 5, z: -12 }, [town], async (x, z) => ground(x, z), () => true, 4);
  expect(spots.length).toBe(4);
  for (const [i, a] of spots.entries()) for (const b of spots.slice(i + 1)) expect(Math.hypot(a.x - b.x, a.z - b.z)).toBeGreaterThanOrEqual(START_CAR_GAP_M);
  // The first is the street start the search picks for the player; the courtyard comes only after the street.
  expect(spots[0].z).toBeCloseTo(0);
  expect(spots.slice(0, 3).every(s => Math.abs(s.z) < 0.01)).toBe(true);
});
