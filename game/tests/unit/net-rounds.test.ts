import { expect, test } from 'vitest';
import { emptyRounds, roundTime, type RoundResult } from '../../src/net/roundTypes';
import { roundPage, resultPage } from '../../src/net/roundMenu';

test('round menu actions reflect electorate and initiator; length changes stay in five-minute steps', () => {
  const state = emptyRounds(), calls: unknown[] = [];
  const page = (self = 'one', minutes = 5) => roundPage(state, self, minutes,
    (...args) => calls.push(args), value => calls.push(value), () => calls.push('history'), () => calls.push('back'));
  page().items.find(i => i.id === 'round-propose')!.run(); expect(calls.pop()).toEqual(['propose']);
  state.proposal = { id: 'p', initiator: 'one', nick: 'First', votes: ['one'], needed: 2, connected: 4 };
  expect(page().items.find(i => i.id === 'round-start')?.enabled).toBe(false);
  expect(page('two').items.some(i => i.id === 'round-start')).toBe(false);
  page('two').items.find(i => i.id === 'round-agree')!.run(); expect(calls.pop()).toEqual(['agree']);
  expect(page().items.find(i => i.id === 'round-shorter')?.enabled).toBe(false);
  page().items.find(i => i.id === 'round-longer')!.run(); expect(calls.pop()).toBe(10);
  state.proposal.votes.push('two');
  const start = page().items.find(i => i.id === 'round-start')!;
  expect(start.enabled).toBe(true); start.run(); expect(calls.pop()).toEqual(['start', 5]);
  expect(page('one', 180).items.find(i => i.id === 'round-longer')?.enabled).toBe(false);
});

test('scoreboard shows authoritative totals and dismiss callback; remaining time clamps at zero', () => {
  let dismissed = false;
  const r: RoundResult = { id: 'r', minutes: 5, start: 0, end: 300000, finished: 300000, phase: 'finished', initiator: 'First', reason: 'time',
    scores: [{ rank: 1, nick: 'First', earned: 42, deliveries: 3, online: true }] };
  const page = resultPage(r, () => { dismissed = true; });
  expect(page.items[0].detail).toContain('€42'); expect(page.items[0].enabled).toBe(false);
  page.items.at(-1)!.run(); expect(dismissed).toBe(true);
  expect(roundTime(300000, 1000)).toBe('4:59'); expect(roundTime(0, 1)).toBe('0:00');
});
