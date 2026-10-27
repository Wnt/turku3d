import { describe, expect, it } from 'vitest';
import { applyHot, markResume, takeResume, RESUME_KEY, type HotPlay } from '../../src/gameplay/hotState';
import { MissionSystem } from '../../src/gameplay/missions/missions';
import { MARKET } from '../../src/gameplay/content/locations';
import { badgeText } from '../../src/ui/buildBadge';
import type { Router } from '../../src/gameplay/contract';
import type { SaveGame } from '../../src/core/save';

const router: Router = { route: (from, to) => [from, to], distance: p => Math.hypot(p[1].x - p[0].x, p[1].z - p[0].z) };

class MemoryStore {
  data = new Map<string, string>();
  getItem(key: string) { return this.data.get(key) ?? null; }
  setItem(key: string, value: string) { this.data.set(key, value); }
  removeItem(key: string) { this.data.delete(key); }
}

/** A mission system that rented the courier permit at the market. */
function rented() {
  const missions = new MissionSystem({ router });
  missions.begin();
  const frame = (extra: object) => ({ position: MARKET.point, interact: false, input: 'keyboard' as const, vehicleId: null, ...extra });
  missions.update(0, frame({ interact: true }));
  missions.update(0, frame({ choose: 'rent_permit' }));
  missions.update(0, frame({ leave: true }));
  return missions;
}

function hotPlay(missions: MissionSystem, extra: Partial<HotPlay> = {}): HotPlay {
  const save: SaveGame = { version: 1, savedAt: 1, label: 'x', mission: missions.serialize(), player: { E: 1, N: 2, H: 3, yaw: 0.5 },
    vehicle: null, clock: { timeOfDay: '2026-06-21T09:30:00.000Z' }, stats: { deliveries: missions.deliveries, money: missions.money } };
  return { save, mode: 'foot', drivenId: null, camera: { yaw: 1.25, pitch: -0.3, distance: 6 }, hudVisible: false, ...extra };
}

describe('hot swap state', () => {
  it('carries money, permit, phase, clock and camera into the new session', () => {
    const before = rented();
    expect(before.permit).toBeGreaterThan(0);
    const after = new MissionSystem({ router });
    const camera = { yaw: 0, pitch: 0, distance: 4.5 };
    let clock: Date | null = null;
    const mode = applyHot(hotPlay(before), { missions: after, setClock: d => { clock = d; }, camera });
    expect({ money: after.money, permit: after.permit, phase: after.phase, vehicle: after.vehicle })
      .toEqual({ money: before.money, permit: before.permit, phase: before.phase, vehicle: before.vehicle });
    expect(clock!.toISOString()).toBe('2026-06-21T09:30:00.000Z');
    expect(camera).toEqual({ yaw: 1.25, pitch: -0.3, distance: 6 });
    expect(mode).toEqual({ mode: 'foot', drivenId: null });
  });

  it('keeps the driven vehicle and leaves missions untouched when the start menu was open', () => {
    const after = new MissionSystem({ router });
    const fresh = after.serialize();
    const camera = { yaw: 0, pitch: 0, distance: 4.5 };
    const mode = applyHot({ ...hotPlay(rented()), save: null, mode: 'car', drivenId: 'scooter' },
      { missions: after, setClock: () => { throw new Error('no clock without a save'); }, camera });
    expect(mode).toEqual({ mode: 'car', drivenId: 'scooter' });
    expect(after.serialize()).toBe(fresh);
  });

  it('the resume marker holds for exactly one page load', () => {
    const storage = new MemoryStore();
    expect(takeResume(storage)).toBe(false);
    markResume(storage);
    expect(storage.getItem(RESUME_KEY)).toBe('1');
    expect(takeResume(storage)).toBe(true);
    expect(takeResume(storage)).toBe(false);
    expect(takeResume(null)).toBe(false);
  });
});

describe('build badge', () => {
  it('names main and the verdict, hidden without the localhost service', () => {
    expect(badgeText({ service: false })).toBeNull();
    const commit = '0123456789abcdef0123456789abcdef01234567';
    expect(badgeText({ service: true, commit, verification: { state: 'unverified' } })).toBe('main 0123456 · unverified');
    expect(badgeText({ service: true, commit, verification: { state: 'unverified', detail: 'verifying: chrome' } }))
      .toBe('main 0123456 · unverified (verifying: chrome)');
    expect(badgeText({ service: true, commit, verification: { state: 'verified' } })).toBe('main 0123456 · verified');
    expect(badgeText({ service: true, commit, verification: { state: 'failed', stage: 'tile-build' } }))
      .toBe('main 0123456 · verification failed: tile-build');
    expect(badgeText({ service: true, commit, verification: { state: 'tests-red', stage: 'chrome' } }))
      .toBe('main 0123456 · tests red: chrome');
    expect(badgeText({ service: true, commit, verification: { state: 'unverified' }, tiles: { key: 'k', source: 'live', building: ['t0_1_1'] } }))
      .toBe('main 0123456 · unverified · building tiles t0_1_1');
  });
});
