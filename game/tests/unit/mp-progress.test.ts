import { expect, test } from 'vitest';
import { MultiplayerProgress } from '../../src/net/progress';
import { MissionSystem } from '../../src/gameplay/missions/missions';
import { freshSave } from '../../src/gameplay/missions/save';
import type { KeyValueStore } from '../../src/core/save';
import type { Router } from '../../src/gameplay/contract';

const a = 'a'.repeat(64), b = 'b'.repeat(64);
const router: Router = { route: (from, to) => [from, to], distance: () => 1 };

function store(): KeyValueStore {
  const data = new Map<string, string>();
  return { getItem: key => data.get(key) ?? null, setItem: (key, value) => { data.set(key, value); },
    removeItem: key => { data.delete(key); } };
}

test('multiplayer mission snapshot survives tab reload for the same server session', () => {
  const storage = store(), progress = new MultiplayerProgress(storage);
  const mission = JSON.stringify({ ...freshSave(12), phase: 'at_pizzeria', money: 148, permit: 4,
    plates: true, kidney: true, warning: true, delivery: 7 });
  progress.save(a, mission);

  const rejoined = new MultiplayerProgress(storage);
  expect(rejoined.load(b)).toBeNull(); // A new server-issued identity must start a fresh mission.
  expect(rejoined.load(null)).toBeNull();
  const restored = new MissionSystem({ router });
  restored.restore(rejoined.load(a));
  expect(restored.money).toBe(148);
  expect(restored.permit).toBe(4);
  expect(restored.plates).toBe(true);
  expect(restored.kidney).toBe(true);
  expect(restored.deliveries).toBe(7);

  rejoined.clear(b);
  expect(rejoined.load(a)).toBe(mission);
  rejoined.clear(a);
  expect(rejoined.load(a)).toBeNull();
});

test('progress storage tolerates invalid tokens, malformed data and blocked storage', () => {
  const storage = store(), progress = new MultiplayerProgress(storage);
  progress.save('not-a-session', '{}');
  expect(progress.load(a)).toBeNull();
  storage.setItem('turku3d.mp.progress.v1', '{broken');
  expect(progress.load(a)).toBeNull();
  const blocked: KeyValueStore = { getItem: () => { throw new Error('blocked'); },
    setItem: () => { throw new Error('blocked'); }, removeItem: () => { throw new Error('blocked'); } };
  const unavailable = new MultiplayerProgress(blocked);
  expect(() => unavailable.save(a, '{}')).not.toThrow();
  expect(unavailable.load(a)).toBeNull();
  expect(() => unavailable.clear(a)).not.toThrow();
});
