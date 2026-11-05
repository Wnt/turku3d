import { describe, expect, it } from 'vitest';
import { parseParams } from '../../src/core/params';
import { statusText, stepEN } from '../../src/qa/preview';

describe('generation preview', () => {
  it('?preview=1 starts a QA camera above the spawn with no traffic or walkers', () => {
    const p = parseParams('?preview=1&traffic=1&walkers=5');
    expect(p.preview).toBe(true);
    expect(p.qa).not.toBeNull();
    expect(p.qa!.E).toBe(p.spawn.E);
    expect(p.qa!.H).toBeGreaterThan(50);
    expect([p.traffic, p.walkers, p.motion]).toEqual([0, 0, true]);
  });
  it('an explicit ?cam= keeps its pose', () => {
    expect(parseParams('?preview=1&cam=1,2,3,4,5,60').qa!.E).toBe(1);
  });
  it('steps along the heading', () => {
    const [e, n] = stepEN(90, 1, 0, 10);
    expect(e).toBeCloseTo(10); expect(n).toBeCloseTo(0);
  });
  it('lists service progress', () => {
    const text = statusText({ state: 'building', building: ['t0_0_1'], queue: ['t0_1_1'], queued: 1 }, ['t0_0_0']);
    expect(text).toContain('building: t0_0_1');
    expect(text).toContain('queued (1): t0_1_1');
    expect(text).toContain('drawn (1): t0_0_0');
  });
  it('a slow or not yet answering service reads as starting or busy, not as no answer', () => {
    expect(statusText(null, [])).toContain('service: starting');
    expect(statusText({ state: 'building' }, [], 12)).toContain('busy, last answer 12 s ago (building)');
  });
});
