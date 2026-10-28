import { expect, test } from 'vitest';
import { ActorCache, ActorUpdates } from '../../src/physics/actorExchange';

test('sleeping actor is published once and retained when later deltas omit it', () => {
  const sender = new ActorUpdates<{ id: string; x: number }>(), receiver = new ActorCache<{ id: string; x: number }>();
  const parked = { id: 'parked', x: 2 }, driven = { id: 'driven', x: 3 };
  const first = sender.sample([parked, driven], 1, 0);
  expect(receiver.apply(first.updates)).toEqual([parked, driven]);
  const changed = { ...driven, x: 4 };
  const second = sender.sample([parked, changed], 2, 1);
  expect(second.updates).toEqual([changed]);
  expect(receiver.apply(second.updates)).toEqual([parked, changed]);
  expect(sender.sample([parked, changed], 3, 2).updates).toEqual([]);
});

test('final pose, break edge and removal survive skipped snapshots until acknowledged', () => {
  const sender = new ActorUpdates<{ id: string; broken: boolean }>();
  const fallen = { id: 'pole', broken: true };
  sender.sample([fallen], 1, 0); // renderer skips this snapshot
  expect(sender.sample([fallen], 2, 0).updates).toEqual([fallen]);
  expect(sender.sample([fallen], 3, 1).updates).toEqual([]);
  expect(sender.sample([], 4, 1).removed).toEqual(['pole']);
  expect(sender.sample([], 5, 1).removed).toEqual(['pole']);
  expect(sender.sample([], 6, 4).removed).toEqual([]);
  expect(sender.sample([fallen], 7, 4).updates).toEqual([fallen]);
});

test('newest pose supersedes an unsampled pose; removals do not erase other cached actors', () => {
  const sender = new ActorUpdates<{ id: string; x: number }>(), receiver = new ActorCache<{ id: string; x: number }>();
  const a = { id: 'a', x: 1 }, b = { id: 'b', x: 1 };
  receiver.apply([a, b]);
  sender.sample([a, b], 1, 0);
  const next = sender.sample([{ ...a, x: 2 }], 2, 1);
  expect(next.updates).toEqual([{ id: 'a', x: 2 }]);
  expect(receiver.apply(next.updates, next.removed)).toEqual([{ id: 'a', x: 2 }]);
});

test('hundreds of unchanged parked poses do not reappear in per-step payloads', () => {
  const sender = new ActorUpdates<{ id: string; pos: { x: number; y: number; z: number } }>();
  const parked = Array.from({ length: 565 }, (_, i) => ({ id: `parked-${i}`, pos: { x: i, y: 2, z: i } }));
  expect(sender.sample(parked, 1, 0).updates).toHaveLength(565);
  const moving = { id: 'player', pos: { x: 1, y: 2, z: 3 } };
  const packet = sender.sample([...parked, moving], 2, 1);
  expect(packet.updates).toEqual([moving]);
  expect(JSON.stringify(packet).length).toBeLessThan(150);
});
