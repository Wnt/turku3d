import { describe, expect, it } from 'vitest';
import { applyPoleFine, LinePool } from '../../src/gameplay/poles/fine';

describe('pole fine', () => {
  it('floors the wallet at zero', () => {
    expect([120, 50, 20, 0].map(applyPoleFine)).toEqual([70, 0, 0, 0]);
  });
  it('uses every line before repeating and avoids a cycle boundary repeat', () => {
    const pool = new LinePool(12, () => 0);
    const first = Array.from({ length: 12 }, () => pool.next());
    expect(new Set(first).size).toBe(12);
    expect(pool.next()).not.toBe(first.at(-1));
  });
});
