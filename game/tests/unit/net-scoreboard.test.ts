import { expect, test } from 'vitest';
import { compactScores } from '../../src/net/scoreboard';
import type { Score } from '../../src/net/client';

const score = (nick: string, rank: number, money: number): Score => ({ nick, rank, money, deliveries: money / 10, online: true });

test('HUD keeps competition-rank podium and adds self once', () => {
  const scores = [score('AA', 1, 30), score('BB', 1, 30), score('CC', 3, 20), score('DD', 4, 10)];
  expect(compactScores(scores, 'DD').map(s => s.nick)).toEqual(['AA', 'BB', 'CC', 'DD']);
  expect(compactScores(scores, 'BB').map(s => s.nick)).toEqual(['AA', 'BB', 'CC']);
  expect(compactScores([], 'BB')).toEqual([]);
});
