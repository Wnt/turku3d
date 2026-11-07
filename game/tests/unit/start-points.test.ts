import { expect, test } from 'vitest';
import { multiplayerStart, startPoints } from '../../src/gameplay/startPoints';
import type { SaveGame } from '../../src/core/save';

const spawn = { x: 170, y: 4, z: 250 }, car = { x: 180, y: 4, z: 260 };
const game = (player: SaveGame['player'], vehicle: SaveGame['vehicle'] = null) => ({ player, vehicle }) as SaveGame;

test('new, migrated, and every selectable save preload their player and owned vehicle in engine coordinates', () => {
  const saved = game({ E: 23460000, N: 6704000, H: 8, yaw: 1 },
    { E: 23460100, N: 6704100, H: 9, yaw: 2, owned: true });
  expect(startPoints(spawn, car, [null, game(null), saved], null)).toEqual([
    spawn, car, { x: 500, y: 8, z: 500 }, { x: 600, y: 9, z: 400 },
  ]);
});

test('multiplayer uses retained local pose before assigned spawn, with no EPSG offset applied twice', () => {
  const you = { spawn: { E: 175, N: -253, yaw: 1 } };
  expect(multiplayerStart(you)).toEqual({ x: 175, y: 0, z: 253, yaw: 1, fresh: true });
  const at = multiplayerStart(you, { pose: { E: 600, N: -700, H: 6, yaw: 2 } });
  expect(at).toEqual({ x: 600, y: 6, z: 700, yaw: 2 });
  expect(startPoints(spawn, car, [], at)).toEqual([spawn, car, at]);
});

test('a retained seat preloads its current position even when the last player pose is stale', () => {
  const you = { spawn: { E: 175, N: -253, yaw: 1 } };
  const self = { pose: { E: 180, N: -250, H: 4, yaw: 0 } };
  expect(multiplayerStart(you, self, { pose: { E: 900, N: -700, H: 6, yaw: 2 } }))
    .toEqual({ x: 900, y: 6, z: 700, yaw: 2 });
});

test('coincident starts share one region and the unowned start car retains its saved parking', () => {
  const saved = game({ E: 23459670, N: 6704250, H: 4, yaw: 0 },
    { E: 23461000, N: 6703000, H: 4, yaw: 0, owned: false });
  expect(startPoints(spawn, car, [saved, saved], spawn)).toEqual([spawn, car, { x: 1500, y: 4, z: 1500 }]);
});

test("a fresh join starts at the city's own start (marked fresh for its start pair), not at the room slot; a retained pose still wins", () => {
  const you = { spawn: { E: 175, N: -253, yaw: 1 } }, start = { x: 150, y: 11.5, z: 360, yaw: 0 };
  expect(multiplayerStart(you, undefined, undefined, start)).toEqual({ ...start, fresh: true });
  expect(multiplayerStart(you, { pose: null }, undefined, start)).toEqual({ ...start, fresh: true });
  expect(multiplayerStart(you, { pose: { E: 160, N: -370, H: 12, yaw: 2 } }, undefined, start)).toEqual({ x: 160, y: 12, z: 370, yaw: 2 });
});

test('a room spawn slot maps to its index in server/spawns.json (0 when unknown)', async () => {
  const { roomSlot } = await import('../../src/gameplay/startPoints');
  const room = (await import('../../../server/spawns.json')).default;
  expect(roomSlot(room.points[3])).toBe(3);
  expect(roomSlot({ E: 1, N: 2 })).toBe(0);
  expect(roomSlot(undefined)).toBe(0);
});
