import { expect, test } from 'vitest';
import { deadlineProgress, musicMuffle, musicScene } from '../../src/audio/music/playState';

test('play contexts map to music states and muffling', () => {
  expect(musicScene(true, false, false)).toBe('menu');
  expect(musicScene(false, false, false)).toBe('explore');
  expect(musicScene(false, false, true)).toBe('mission');
  expect(musicScene(false, true, true)).toBe('menu');
  expect(musicScene(false, false, false)).toBe('explore');
  expect(musicMuffle(false, true, false)).toBe(1);
  expect(musicMuffle(true, false, false)).toBe(1);
  expect(musicMuffle(false, false, true)).toBe(0.6);
  expect(musicMuffle(false, false, false)).toBe(0);
});

test('delivery progress uses elapsed over par with safe bounds', () => {
  expect(deadlineProgress(0, 120)).toBe(0);
  expect(deadlineProgress(60, 120)).toBe(0.5);
  expect(deadlineProgress(180, 120)).toBe(1);
  expect(deadlineProgress(-10, 120)).toBe(0);
  expect(deadlineProgress(5, 0)).toBe(0);
});
