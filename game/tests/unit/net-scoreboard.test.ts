import { expect, test } from 'vitest';
import { compactScores } from '../../src/net/scoreboard';
import type { Score } from '../../src/net/client';

const score = (nick: string, rank: number, earned: number): Score => ({ nick, rank, earned, deliveries: earned / 10, online: true });

test('HUD keeps competition-rank podium and adds self once', () => {
  const scores = [score('TurboHerring', 1, 30), score('SneakyPizza', 1, 30), score('SunnyScooter', 3, 20), score('HappyFerry', 4, 10)];
  expect(compactScores(scores, 'HappyFerry').map(s => s.nick)).toEqual(['TurboHerring', 'SneakyPizza', 'SunnyScooter', 'HappyFerry']);
  expect(compactScores(scores, 'SneakyPizza').map(s => s.nick)).toEqual(['TurboHerring', 'SneakyPizza', 'SunnyScooter']);
  expect(compactScores([], 'SneakyPizza')).toEqual([]);
  expect(compactScores(scores.map(s => ({ ...s, rank: 1 })), 'HappyFerry').map(s => s.nick))
    .toEqual(['TurboHerring', 'SneakyPizza', 'SunnyScooter', 'HappyFerry']);
});


test('crowded HUD stays at four rows and preserves offline self and actual rank', () => {
  const scores = Array.from({ length: 32 }, (_, i) => score(`SunnyPlayer${i}`, i + 1, 320 - i * 10));
  scores[31] = { ...scores[31], nick: 'SparklySquirrel', online: false };
  expect(compactScores(scores, 'SparklySquirrel')).toEqual([...scores.slice(0, 3), scores[31]]);
  expect(compactScores(scores, null)).toEqual(scores.slice(0, 3));
});

test('archives retain recorded totals and ranks for both earned and legacy money schemas', async () => {
  const { normalizeArchivedScore } = await import('../../src/net/scoreboard');
  const current = score('TurboHerring', 1, 30);
  expect(normalizeArchivedScore(current)).toEqual(current);
  const old = { nick: 'AB', rank: 3, money: -100, deliveries: 2, online: false };
  expect(normalizeArchivedScore(old)).toEqual({ ...old, earned: -100 });
});
