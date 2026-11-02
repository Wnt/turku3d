import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { PLAYER_LOCOMOTION_SHA256 } from '../../src/actors/locomotion';
import { expect, test } from 'vitest';
import { locomotionFrequency, locomotionWeights, LOCOMOTION } from '../../src/actors/locomotion';

test('speed-matched gait keeps plausible cadence from a slow walk through a sprint', () => {
  // Two steps per cycle. The old universal 1.8 m stride reached 6.7 steps/s at sprint.
  for (const speed of [0.2, 0.75, 1.5, 2.5, 3.6, 4.8, 6]) {
    const weights = locomotionWeights(speed), hz = locomotionFrequency(speed, weights, 0);
    expect(weights.reduce((sum, w) => sum + w, 0)).toBeCloseTo(1);
    expect(hz * 2).toBeGreaterThan(1);
    expect(hz * 2).toBeLessThan(3.5);
    const travel = LOCOMOTION.reduce((sum, clip, i) => sum + clip.travel * weights[i], 0);
    expect(travel * hz).toBeCloseTo(speed, 5);
  }
  expect(locomotionFrequency(0, locomotionWeights(0), 0)).toBe(0);
  const cadence = [1.5, 2.5, 3.6, 4.8, 6].map(s => locomotionFrequency(s, locomotionWeights(s), 0));
  expect(cadence).toEqual([...cadence].sort((a, b) => a - b));
});

test('stairs blend monotonically into the shorter formal stride', () => {
  const weights = locomotionWeights(1.5);
  const rates = [0, 0.25, 0.5, 0.75, 1].map(blend => locomotionFrequency(1.5, weights, blend));
  expect(rates).toEqual([...rates].sort((a, b) => a - b));
  for (let i = 1; i < rates.length; i++) expect(rates[i] - rates[i - 1]).toBeLessThan(0.08);
});

test('stride calibration belongs to the shipped character asset', () => {
  const bytes = readFileSync(new URL('../../public/assets/character/player.glb', import.meta.url));
  expect(createHash('sha256').update(bytes).digest('hex')).toBe(PLAYER_LOCOMOTION_SHA256);
});
