// Developer tools (localhost only): the gate, the fly controller, dev money in the save, the time-of-day phases,
// the teleport table and the pause menu's Developer pages.
import { describe, expect, test } from 'vitest';
import { devToolsEnabled } from '../../src/core/localOnly';
import { FLY, FlyMode } from '../../src/gameplay/flyMode';
import { SPEED } from '../../src/actors/player';
import { MissionSystem, missionSummary } from '../../src/gameplay/missions/missions';
import { START_MONEY } from '../../src/gameplay/economy';
import { SaveStore, formatDetails, type KeyValueStore, type SaveGame } from '../../src/core/save';
import { phaseTime, sunPosition } from '../../src/core/sun';
import { PLACES, parsePlace } from '../../src/gameplay/content/places';
import { DevMenuFlow, type DevHooks } from '../../src/ui/devMenu';
import { SaveMenuFlow, type GameHooks, type MenuPage } from '../../src/ui/menu';
import type { Router } from '../../src/gameplay/contract';

const loc = (url: string) => { const u = new URL(url); return { hostname: u.hostname, search: u.search }; };
const router: Router = { route: (from, to) => [from, to], distance: p => Math.hypot(p[1].x - p[0].x, p[1].z - p[0].z) };

describe('gate', () => {
  test('on a local origin or with ?dev=1, never on GitHub Pages', () => {
    expect(devToolsEnabled(loc('http://localhost:5171/'))).toBe(true);
    expect(devToolsEnabled(loc('http://127.0.0.1:5351/?backend=webgl'))).toBe(true);
    expect(devToolsEnabled(loc('http://[::1]:5171/'))).toBe(true);
    expect(devToolsEnabled(loc('http://localhost:5171/?dev=0'))).toBe(false);
    expect(devToolsEnabled(loc('https://wnt.github.io/turku-game/game/'))).toBe(false);
    expect(devToolsEnabled(loc('https://wnt.github.io/turku-game/game/?dev=1'))).toBe(false);
    expect(devToolsEnabled(loc('https://example.org/'))).toBe(false);
    expect(devToolsEnabled(loc('https://example.org/?dev=1'))).toBe(true);
  });
});

describe('fly controller', () => {
  test('a double tap of jump toggles flight; slow taps do not', () => {
    const fly = new FlyMode();
    expect(fly.tap(10)).toBe(false);
    expect(fly.tap(10 + FLY.doubleTap + 0.01)).toBe(false);
    expect(fly.active).toBe(false);
    expect(fly.tap(10 + FLY.doubleTap + 0.2)).toBe(true);
    expect(fly.active).toBe(true);
    // The tap that toggled does not start the next double tap.
    expect(fly.tap(10 + FLY.doubleTap + 0.4)).toBe(false);
    expect(fly.tap(10 + FLY.doubleTap + 0.6)).toBe(true);
    expect(fly.active).toBe(false);
    fly.set(true);
    expect(fly.active).toBe(true);
  });
  test('three times walking speed, ten times with the fast modifier, in every direction', () => {
    const fly = new FlyMode();
    expect(FLY.speed).toBeCloseTo(3 * SPEED.walk);
    expect(FLY.fastSpeed).toBeCloseTo(10 * SPEED.walk);
    expect(fly.velocity({ x: 1, z: 0, up: 0, down: 0, fast: false })).toEqual({ vx: FLY.speed, vy: 0, vz: 0 });
    expect(fly.velocity({ x: 0, z: -1, up: 1, down: 0, fast: true })).toEqual({ vx: 0, vy: FLY.fastSpeed, vz: -FLY.fastSpeed });
    expect(fly.velocity({ x: 0, z: 0, up: 0, down: 1, fast: false }).vy).toBe(-FLY.speed);
    expect(fly.velocity({ x: 0, z: 0, up: 1, down: 1, fast: false }).vy).toBe(0);
    const diagonal = fly.velocity({ x: 1, z: 1, up: 0, down: 0, fast: false });
    expect(Math.hypot(diagonal.vx, diagonal.vz)).toBeCloseTo(FLY.speed);
    const half = fly.velocity({ x: 0.5, z: 0, up: 0.5, down: 0, fast: false });
    expect(half).toEqual({ vx: FLY.speed / 2, vy: FLY.speed / 2, vz: 0 });
  });
});

describe('dev money', () => {
  class MemoryStore implements KeyValueStore {
    data = new Map<string, string>();
    getItem(key: string) { return this.data.get(key) ?? null; }
    setItem(key: string, value: string) { this.data.set(key, value); }
    removeItem(key: string) { this.data.delete(key); }
  }
  test('goes to the local wallet, is marked devMoney in the mission save and survives a reload; no paid drop-off', () => {
    const paid: unknown[] = [];
    const missions = new MissionSystem({ router, seed: 1, onPaid: drop => paid.push(drop) });
    missions.grantDevMoney(1000);
    missions.grantDevMoney(10_000);
    expect(missions.money).toBe(START_MONEY + 11_000);
    expect(missions.devMoney).toBe(11_000);
    expect(missions.snapshot().hud.money).toBe(START_MONEY + 11_000);
    expect(paid).toEqual([]);
    const text = missions.serialize();
    expect(JSON.parse(text)).toMatchObject({ money: START_MONEY + 11_000, devMoney: 11_000 });
    expect(missionSummary(text)).toMatchObject({ money: START_MONEY + 11_000, devMoney: 11_000 });
    const reloaded = new MissionSystem({ router, seed: 2 });
    reloaded.restore(text);
    expect(reloaded.devMoney).toBe(11_000);
  });
  test('a save from before the tools reads as no dev money', () => {
    const missions = new MissionSystem({ router, seed: 1 });
    const old = JSON.parse(missions.serialize());
    delete old.devMoney;
    missions.restore(JSON.stringify(old));
    expect(missions.devMoney).toBe(0);
    expect(missions.money).toBe(START_MONEY);
    missions.restore(JSON.stringify({ ...old, devMoney: -5 }));
    expect(missions.devMoney).toBe(0); // invalid: a new game
  });
  test('the save slot keeps stats.devMoney and its details show it', () => {
    const storage = new MemoryStore();
    const store = new SaveStore(storage, missionSummary);
    const game: SaveGame = { version: 1, savedAt: 1, label: 'x', mission: '{}', player: null, vehicle: null, clock: null,
      stats: { deliveries: 2, money: 10_120, devMoney: 10_000 } };
    store.write('slot1', game);
    expect(new SaveStore(storage, missionSummary).get('slot1')?.stats.devMoney).toBe(10_000);
    expect(formatDetails(game, 'en-GB')).toMatch(/^2 deliveries · 10120 € \(10000 € dev\) · saved /);
    expect(formatDetails({ ...game, stats: { deliveries: 2, money: 120 } }, 'en-GB')).toMatch(/^2 deliveries · 120 € · saved /);
    store.write('slot2', { ...game, stats: { deliveries: 0, money: 1, devMoney: Number.NaN } });
    expect(new SaveStore(storage, missionSummary).has('slot2')).toBe(false);
  });
});

describe('time of day', () => {
  const day = new Date('2026-10-15T14:00:00+03:00');
  const hour = (d: Date) => (d.getTime() - new Date('2026-10-15T00:00:00+03:00').getTime()) / 3_600_000;
  test('dawn, noon, dusk and the night after, in order, with the sun where it should be', () => {
    const [dawn, noon, dusk, night] = (['dawn', 'noon', 'dusk', 'night'] as const).map(p => phaseTime(day, p));
    expect(hour(dawn)).toBeGreaterThan(7.5); expect(hour(dawn)).toBeLessThan(9.5);
    expect(hour(noon)).toBeGreaterThan(12.9); expect(hour(noon)).toBeLessThan(13.6);
    expect(hour(dusk)).toBeGreaterThan(17); expect(hour(dusk)).toBeLessThan(18.5);
    expect(hour(night)).toBeGreaterThan(24); expect(hour(night)).toBeLessThan(26); // the coming night
    expect(sunPosition(dawn).elevation).toBeCloseTo(4, 0);
    expect(sunPosition(dusk).elevation).toBeCloseTo(4, 0);
    expect(sunPosition(noon).elevation).toBeGreaterThan(20);
    expect(sunPosition(night).elevation).toBeLessThan(-30);
    // Any time of that day gives the same moments.
    expect(phaseTime(new Date('2026-10-15T23:30:00+03:00'), 'noon').getTime()).toBe(noon.getTime());
  });
  test('deep winter: dawn and dusk fall back to three hours around noon when the sun stays low', () => {
    const winter = new Date('2026-12-21T12:00:00+02:00');
    const noon = phaseTime(winter, 'noon');
    expect(sunPosition(noon).elevation).toBeLessThan(7);
    expect(phaseTime(winter, 'dawn').getTime()).toBeLessThan(noon.getTime());
    expect(phaseTime(winter, 'dusk').getTime()).toBeGreaterThan(noon.getTime());
  });
});

describe('teleport places', () => {
  test('the named places, in EPSG:3877 around Turku', () => {
    expect(PLACES.map(p => p.label)).toEqual(['Office', 'Pizzeria', 'Tuomiokirkko', 'Turun linna', 'Kauppatori', 'Aurakatu 6a']);
    for (const p of PLACES) {
      expect(p.E).toBeGreaterThan(23_455_000); expect(p.E).toBeLessThan(23_465_000);
      expect(p.N).toBeGreaterThan(6_700_000); expect(p.N).toBeLessThan(6_708_000);
    }
  });
  test('Enter E,N reads E,N or E,N,heading', () => {
    expect(parsePlace('23459668, 6704254')).toEqual({ E: 23459668, N: 6704254, headingDeg: 0 });
    expect(parsePlace('23459668,6704254,90')).toEqual({ E: 23459668, N: 6704254, headingDeg: 90 });
    expect(parsePlace('23459668')).toBeNull();
    expect(parsePlace('a,b')).toBeNull();
    expect(parsePlace('1,2,3,4')).toBeNull();
    expect(parsePlace('1,,2')).toBeNull();
  });
});

describe('Developer pages', () => {
  function setup(answer: string | null = null) {
    const shown: MenuPage[] = [];
    const host = { show: (page: MenuPage) => { shown.push(page); } };
    const calls: string[] = [];
    let money = 120, flying = false;
    const hooks: DevHooks = {
      money: () => money, addMoney: euros => { money += euros; calls.push(`money ${euros}`); },
      flying: () => flying, setFlying: on => { flying = on; calls.push(`fly ${on}`); },
      teleport: t => calls.push(`teleport ${t.E},${t.N},${t.headingDeg}`), built: E => E > 23_459_000 && E < 23_459_800,
      ask: () => answer, setTime: phase => calls.push(`time ${phase}`),
    };
    const root: MenuPage = { id: 'pause', title: 'Paused', text: null, back: null, items: [] };
    const flow = new DevMenuFlow(host, hooks, () => root);
    const pick = (page: MenuPage, id: string) => page.items.find(i => i.id === id)!.run();
    return { flow, shown, calls, pick, root, last: () => shown[shown.length - 1] };
  }
  test('money, fly mode, time of day and back', () => {
    const { flow, calls, pick, last, root } = setup();
    const page = flow.root();
    expect(page.items.map(i => i.label)).toEqual(['Money +1 000 €', 'Money +10 000 €', 'Fly mode on', 'Teleport to...',
      'Time of day', 'Back']);
    expect(page.text).toBe('Money 120 €.');
    pick(page, 'money-10000');
    expect(last().text).toBe('Money 10 120 €.');
    pick(last(), 'fly');
    expect(last().text).toBe('Fly mode on.');
    expect(last().items.find(i => i.id === 'fly')!.label).toBe('Fly mode off');
    pick(last(), 'time');
    expect(last().items.map(i => i.label)).toEqual(['Dawn', 'Noon', 'Dusk', 'Night', 'Back']);
    pick(last(), 'time-dusk');
    expect(last().text).toBe('Dusk.');
    expect(calls).toEqual(['money 10000', 'fly true', 'time dusk']);
    last().back!();
    expect(last()).toBe(root);
  });
  test('teleport: named places (unbuilt ones arrive flying) and Enter E,N', () => {
    const { flow, calls, pick } = setup('23459700,6704300,45');
    const page = flow.teleport();
    expect(page.items.map(i => i.label)).toEqual([...PLACES.map(p => p.label), 'Enter E,N', 'Back']);
    expect(page.items.find(i => i.id === 'place-office')!.detail).toBeNull();
    expect(page.items.find(i => i.id === 'place-turun-linna')!.detail).toBe('Not built: arrives flying');
    pick(page, 'place-pizzeria');
    pick(page, 'enter');
    expect(calls).toEqual(['teleport 23459577.1,6704312.4,0', 'teleport 23459700,6704300,45']);
    const bad = setup('nonsense');
    bad.pick(bad.flow.teleport(), 'enter');
    expect(bad.last().text).toBe('Not E,N: nonsense');
    expect(bad.calls).toEqual([]);
    const cancelled = setup(null);
    cancelled.pick(cancelled.flow.teleport(), 'enter');
    expect(cancelled.calls).toEqual([]);
    expect(cancelled.shown).toEqual([]);
  });
  test('the pause menu lists Developer before Quit only where the tools run', () => {
    const store = new SaveStore(null, missionSummary);
    const hooks: GameHooks = { capture: () => { throw new Error('unused'); }, play: () => {}, resume: () => {}, quit: () => {} };
    const host = { show: () => {} };
    let opened = 0;
    const withDev = new SaveMenuFlow(host, store, hooks, undefined, undefined, () => { opened++; }).pause();
    expect(withDev.items.map(i => i.id)).toEqual(['resume', 'save', 'load', 'new', 'dev', 'quit']);
    withDev.items.find(i => i.id === 'dev')!.run();
    expect(opened).toBe(1);
    expect(new SaveMenuFlow(host, store, hooks).pause().items.map(i => i.id)).not.toContain('dev');
  });
});
