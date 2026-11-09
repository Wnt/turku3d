import { describe, expect, it, vi } from 'vitest';
import { decodeCtl, encodeCtl, motionOf, spinOf, UNDRIVEN, validCtl } from '../../src/net/motion';

describe('car motion on the wire (net/motion.ts)', () => {
  it('round-trips a driver input through ctl within its quantization', () => {
    const ctl = encodeCtl({ throttle: 0.734, brake: 0, steer: -0.4126, handbrake: true, steerKey: true });
    expect(ctl).toEqual([73, 0, -413, 7]);
    expect(validCtl(ctl)).toBe(true);
    expect(decodeCtl(ctl)).toEqual({ throttle: 0.73, brake: 0, steer: -0.413, handbrake: true, steerKey: true });
  });
  it('an undriven car sends zeros and decodes to no input; out-of-range input is clamped', () => {
    expect(encodeCtl(null)).toEqual(UNDRIVEN);
    expect(decodeCtl(UNDRIVEN)).toBeNull();
    expect(decodeCtl(undefined)).toBeNull();
    expect(encodeCtl({ throttle: 3, brake: -1, steer: NaN, handbrake: false })).toEqual([100, 0, 0, 4]);
  });
  it('spin goes to E, N, H and back to the engine frame', () => {
    const m = motionOf({ angvel: { x: 0.1, y: 1.2, z: -0.3 }, controls: undefined });
    expect(m).toEqual({ w: [0.1, 0.3, 1.2], ctl: [0, 0, 0, 0] });
    expect(spinOf(m.w)).toEqual({ x: 0.1, y: 1.2, z: -0.3 });
    expect(spinOf([0, 0, 1000])).toBeNull();
  });
});

describe('room clock (net/client.ts syncClock)', () => {
  it('keeps the least-delayed serverTime sample, so one late read does not skew it', async () => {
    const { MultiplayerClient } = await import('../../src/net/client');
    const client = new MultiplayerClient('ws://localhost:1/') as unknown as { syncClock(t: number): void; serverNow(): number };
    let now = 1000;
    const spy = vi.spyOn(performance, 'now').mockImplementation(() => now);
    // True offset 5000 ms. Join read 250 ms late, then samples 40, 5 and 120 ms late.
    client.syncClock(1000 + 5000 - 250);
    expect(client.serverNow()).toBe(1000 + 4750);
    for (const [at, late] of [[1050, 40], [1100, 5], [1150, 120]]) { now = at; client.syncClock(at + 5000 - late); }
    expect(client.serverNow() - now).toBeCloseTo(4995, 0);
    spy.mockRestore();
  });
});
