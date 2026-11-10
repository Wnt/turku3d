import { afterEach, expect, test, vi } from 'vitest';
import { SaveGameController, type Start } from '../../src/gameplay/saveGame';
import { ROOM_START_CARS, multiplayerStart, roomSlot } from '../../src/gameplay/startPoints';
import { resetRoundGame } from '../../src/net/roundReset';
import { SaveStore } from '../../src/core/save';
import { missionSummary } from '../../src/gameplay/missions/missions';
import { findInitialGround, insideCar, startPairs, START_CAR_GAP_M } from '../../src/world/startGround';
import { MAT } from '../../src/shared/col/materials';
import type { RoadGraphJson } from '../../src/gameplay/roadGraph';
import room from '../../../server/spawns.json';

afterEach(() => { vi.unstubAllGlobals(); });

// The page was entered at a portal exit (`?spawn=`, 1.6 km from the office); the city's start is at the office.
const portal: Start = { spawn: { x: 1785, y: 9, z: -47, yaw: 2 }, car: { x: 1790, y: 9, z: -50, yaw: 1 } };
const office: Start = { spawn: { x: 168, y: 4, z: 246, yaw: 0 }, car: { x: 172, y: 4, z: 250, yaw: 0.5 } };
/** The start pair of room slot `slot` at `start`: 7 m apart along x, as startPairs would scatter them. */
const pairOf = (start: Start, slot: number): Start => ({ spawn: { ...start.spawn, x: start.spawn.x + 7 * slot },
  car: { ...start.car, x: start.car.x + 7 * slot } });

function session(slot: number | null, portalPage = true) {
  vi.stubGlobal('window', new EventTarget());
  vi.stubGlobal('document', new EventTarget());
  const latest = { vehicles: [] as { id: string; pos: { x: number; y: number; z: number } }[], character: { pos: { x: 0, y: 0, z: 0 } } };
  const physics = { latest, loaded: new Map(), step: vi.fn(), setVehicleDamage: vi.fn(), removeVehicle: vi.fn(),
    resetVehicle: vi.fn((pos, _yaw, id) => { latest.vehicles = [{ id, pos }]; }),
    setCharacterEnabled: vi.fn((enabled, pos) => { if (enabled && pos) latest.character.pos = pos; }),
    raycast: vi.fn(async (origin: { y: number }) => ({ point: { y: origin.y > 100 ? 4 : origin.y - 2 } })) };
  const streamed: [number, number][] = [];
  const cityStart = vi.fn(async (stream: (x: number, z: number) => Promise<void>) => {
    await stream(office.spawn.x, office.spawn.z);
    return office;
  });
  const starts: Start[] = [];
  const save = new SaveGameController({
    physics, streamer: { update: (x: number, z: number) => { streamed.push([x, z]); } }, manifest: { collision: {} },
    missions: { vehicle: 'none', restore: vi.fn(), begin: vi.fn() }, recovery: { restart: vi.fn() },
    saves: new SaveStore(null, missionSummary), startScreen: { close: vi.fn(), active: false }, pauseMenu: { close: vi.fn() },
    ...portal, ...(portalPage ? { cityStart } : {}),
    view: () => ({ mode: 'foot', drivenId: null, feet: latest.character.pos, cameraYaw: 0 }), placed: vi.fn(), setPaused: vi.fn(),
    showIntro: async () => {}, fail: (e: unknown) => { throw e; },
    // play.ts: a solo player takes the start itself, a room's player the pair of its slot.
    startPair: async (start: Start) => { starts.push(start); return slot ? pairOf(start, slot) : null; },
  } as unknown as ConstructorParameters<typeof SaveGameController>[0]);
  const feet = () => ({ x: latest.character.pos.x, z: latest.character.pos.z });
  const car = () => { const p = latest.vehicles.find(v => v.id === 'player')!.pos; return { x: p.x, z: p.z }; };
  const xz = (p: { x: number; z: number }) => ({ x: p.x, z: p.z });
  return { save, physics, cityStart, streamed, starts, feet, car, xz };
}
const you = (slot: number) => ({ spawn: room.points[slot] });
// play.ts `roomStart`: the page's own start (here the portal exit), marking a start as fresh.
const roomStart = portal.spawn;

test('a portal arrival is placed at the exit pose once; respawn at the start then goes to the city start', async () => {
  const h = session(null);
  await h.save.playAt({ ...portal.spawn, fresh: true });
  expect(h.feet()).toEqual(h.xz(portal.spawn));
  expect(h.car()).toEqual(h.xz(portal.car));
  expect(h.cityStart).not.toHaveBeenCalled();
  await h.save.respawn();
  expect(h.feet()).toEqual(h.xz(office.spawn));
  // The start's surroundings stream in before the start is searched.
  expect(h.streamed).toContainEqual([office.spawn.x, office.spawn.z]);
  await h.save.respawn();
  expect(h.cityStart).toHaveBeenCalledTimes(1);
  h.save.dispose();
});

test('after a portal arrival every start in a room is the city start pair of the own slot: round start, respawn, reset progress', async () => {
  const placed: { x: number; z: number }[][] = [];
  for (const slot of [1, 2, 5]) {
    const h = session(slot), spots: { x: number; z: number }[] = [];
    // The arrival: a fresh start at the exit pose, on the slot's pair there.
    await h.save.playAt({ ...portal.spawn, fresh: true });
    expect(h.feet()).toEqual(h.xz(pairOf(portal, slot).spawn));
    // A timed round starts (net/roundReset.ts).
    const shared = { resetRound: vi.fn() };
    await resetRoundGame(you(slot), shared as never, h.physics as never, h.save, () => {}, roomStart);
    expect(h.feet()).toEqual(h.xz(pairOf(office, slot).spawn));
    expect(h.car()).toEqual(h.xz(pairOf(office, slot).car));
    spots.push(h.feet());
    // The pause menu's respawn at the start.
    await h.save.playAt({ x: 900, y: 4, z: 900, yaw: 0 });
    await h.save.respawn();
    expect(h.feet()).toEqual(h.xz(pairOf(office, slot).spawn));
    // Reset progress in the room (play.ts resetProgress.at).
    await h.save.playAt({ x: 900, y: 4, z: 900, yaw: 0 });
    await h.save.resetProgress(async () => true, () => multiplayerStart(you(slot), undefined, undefined, roomStart));
    expect(h.feet()).toEqual(h.xz(pairOf(office, slot).spawn));
    expect(h.starts.slice(1).every(s => s === office)).toBe(true);
    placed.push(spots);
    h.save.dispose();
  }
  expect(new Set(placed.map(s => `${s[0].x},${s[0].z}`)).size).toBe(3);
});

test('solo after a portal arrival: reset progress and a new game start at the city start itself', async () => {
  const h = session(null);
  await h.save.playAt({ ...portal.spawn, fresh: true });
  await h.save.resetProgress(async () => true, () => null);
  await vi.waitFor(() => expect(h.save.loading).toBe(false));
  expect(h.feet()).toEqual(h.xz(office.spawn));
  expect(h.car()).toEqual(h.xz(office.car));
  await h.save.playAt({ x: 900, y: 4, z: 900, yaw: 0 });
  h.save.hooks.play(null);
  await vi.waitFor(() => expect(h.save.loading).toBe(false));
  expect(h.feet()).toEqual(h.xz(office.spawn));
  h.save.dispose();
});

test("a page without ?spawn= keeps its own start for every start (another city's centre, Turku's office)", async () => {
  const h = session(3, false);
  await h.save.playAt(multiplayerStart(you(3), undefined, undefined, roomStart));
  await h.save.respawn();
  expect(h.feet()).toEqual(h.xz(pairOf(portal, 3).spawn));
  expect(h.starts.every(s => s.spawn === portal.spawn && s.car === portal.car)).toBe(true);
  h.save.dispose();
});

test('sixteen room slots take sixteen start pairs: no two cars within the gap, each player beside its own car only', async () => {
  expect(ROOM_START_CARS).toBe(16);
  expect(new Set(room.points.map(p => roomSlot(p))).size).toBe(16);
  // A street grid round the start: three east-west streets 40 m apart and two cross streets.
  const STREET = 2 | 4, line = (z: number) => [-90, z, 90, z];
  const nodes = [...line(0), ...line(40), ...line(-40), -30, -40, -30, 40, 30, -40, 30, 40];
  const town: RoadGraphJson = { format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', bbox: [-100, -50, 100, 50],
    nodes, nodeFlags: nodes.filter((_, i) => i % 2 === 0).map(() => 32),
    edges: [[0, 1, STREET, 180], [2, 3, STREET, 180], [4, 5, STREET, 180], [6, 7, STREET, 80], [8, 9, STREET, 80]] };
  const probe = async (x: number, z: number) => ({ point: { x, y: 4, z }, normal: { x: 0, y: 1, z: 0 }, material: MAT.road, toi: 496, tile: 'c_0_0' });
  const first = (await findInitialGround({ x: 5, z: -3 }, [town], probe, () => true))!;
  const pairs = await startPairs(first, [town], probe, () => true, ROOM_START_CARS);
  expect(pairs.length).toBe(16);
  let nearest = Infinity;
  for (const [i, a] of pairs.entries()) for (const [j, b] of pairs.entries()) if (i < j) {
    expect(Math.hypot(a.car.x - b.car.x, a.car.z - b.car.z)).toBeGreaterThanOrEqual(START_CAR_GAP_M);
    expect(insideCar(b.car, a.spawn.x, a.spawn.z, 0.5)).toBe(false);
    expect(insideCar(a.car, b.spawn.x, b.spawn.z, 0.5)).toBe(false);
    nearest = Math.min(nearest, Math.hypot(a.spawn.x - b.spawn.x, a.spawn.z - b.spawn.z));
  }
  expect(nearest).toBeGreaterThan(1);
});
