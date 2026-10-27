import { afterEach, expect, test, vi } from 'vitest';
import { featureFlag } from '../../src/core/flags';

afterEach(() => vi.unstubAllGlobals());

test('A/B settings require an explicit 1 and default to off', () => {
  vi.stubGlobal('location', { search: '?ab.example=1&ab.other=true' });
  expect(featureFlag('example')).toBe(true);
  expect(featureFlag('other')).toBe(false);
  expect(featureFlag('absent')).toBe(false);
});
