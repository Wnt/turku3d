// Developer tools (localhost only): the gate, the fly controller, dev money in the save, the developer clock and its
// quick times, the teleport table and the pause menu's Developer pages.
import { describe, expect, test } from 'vitest';
import { devToolsEnabled } from '../../src/core/localOnly';
import { FLY, FlyMode } from '../../src/gameplay/flyMode';
import { SPEED } from '../../src/actors/player';
import { MissionSystem, missionSummary } from '../../src/gameplay/missions/missions';
import { START_MONEY } from '../../src/gameplay/economy';
import { SaveStore, formatDetails, type KeyValueStore, type SaveGame } from '../../src/core/save';
import { CYCLE_EPOCH, DAY_MS, cycleAt, cycleHour, formatClock } from '../../src/core/dayCycle';
import { DayClock, type ClockMode } from '../../src/render/dayClock';
import type { Environment } from '../../src/render/environment';
import { nightFactor } from '../../src/render/timecycle';
import { TIME_PRESETS, clockReadout } from '../../src/ui/timePanel';
import { PLACES, parsePlace } from '../../src/gameplay/content/places';
import { DevMenuFlow, type DevHooks } from '../../src/ui/devMenu';
import { SaveMenuFlow, type GameHooks, type MenuPage } from '../../src/ui/menu';
import type { Router } from '../../src/gameplay/contract';

const loc = (url: string) => { const u = new URL(url); return { hostname: u.hostname, search: u.search }; };
const router: Router = { route: (from, to) => [from, to], distance: p => Math.hypot(p[1].x - p[0].x, p[1].z - p[0].z) };

describe('gate', () => {
  test('on a local origin or with ?dev=1, also on GitHub Pages', () => {
    expect(devToolsEnabled(loc('http://localhost:5171/'))).toBe(true);
    expect(devToolsEnabled(loc('http://127.0.0.1:5351/?backend=webgl'))).toBe(true);
    expect(devToolsEnabled(loc('http://[::1]:5171/'))).toBe(true);
    expect(devToolsEnabled(loc('http://localhost:5171/?dev=0'))).toBe(false);
    expect(devToolsEnabled(loc('https://wnt.github.io/turku-game/game/'))).toBe(false);
    expect(devToolsEnabled(loc('https://wnt.github.io/turku-game/game/?dev=1'))).toBe(true);
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
    expect(FLY.speed).toBeCloseTo(48 * SPEED.walk);
    expect(FLY.fastSpeed).toBeCloseTo(4 * FLY.speed);
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

/** The Environment surface DayClock drives, recording sun states and IBL refreshes like the real one (skyDrift). */
function fakeEnv() {
  const env = {
    sunElevation: 90, nightFactor: 0, shadowsFrozen: false, hour: 12, iblElevation: Number.NaN, ibl: [] as number[], suns: 0,
    setSun(_azimuth: number, elevation: number, hour: number) { env.sunElevation = elevation; env.hour = hour; env.nightFactor = nightFactor(elevation); env.suns++; },
    setTime() { env.setSun(0, 30, 14); },
    skyDrift() { return Number.isNaN(env.iblElevation) ? Infinity : Math.abs(Math.max(env.sunElevation, -12) - Math.max(env.iblElevation, -12)); },
    updateIBL() { env.iblElevation = env.sunElevation; env.ibl.push(env.sunElevation); },
  };
  return env;
}
function devClock(mode?: ClockMode, start = CYCLE_EPOCH + 10 * 60_000) {
  const env = fakeEnv();
  const time = { now: start };
  const clock = new DayClock(env as unknown as Environment, mode, false, () => time.now);
  const advance = (ms: number, frame = 1000 / 60) => { for (let t = 0; t < ms; t += frame) { time.now += Math.min(frame, ms - t); clock.update(); } };
  return { clock, env, time, advance };
}
const MIN = 1 / 60;

describe('time of day', () => {
  test('the quick times sit where their names say on the cycle\'s sun path', () => {
    expect(TIME_PRESETS.map(p => `${p.label} ${formatClock(p.hour)}`)).toEqual(['Dawn 06:30', '09:00 09:00', '13:00 13:00',
      'Golden hour 19:30', 'Blue hour 20:30', '22:00 22:00', '04:00 04:00']);
    const sun = Object.fromEntries(TIME_PRESETS.map(p => [p.id, cycleAt(p.hour)]));
    expect(Math.abs(sun.dawn.sun.elevation)).toBeLessThan(4);
    expect(sun['0900'].sun.elevation).toBeGreaterThan(10); expect(sun['1300'].sun.elevation).toBeGreaterThan(30);
    expect(sun.golden.sun.elevation).toBeGreaterThan(0); expect(sun.golden.sun.elevation).toBeLessThan(6);
    expect(sun.blue.sun.elevation).toBeLessThan(-2); expect(sun.blue.sun.elevation).toBeGreaterThan(-6);
    for (const id of ['2200', '0400']) { expect(sun[id].sun.elevation).toBeLessThan(-12); expect(sun[id].night).toBe(1); }
    expect(sun['1300'].night).toBe(0);
  });

  test('pause, ×600, seek while running, wrapping steps and back to the live clock', () => {
    const { clock, time, advance } = devClock();
    expect(clock.local).toBe(false); expect(clock.running).toBe(true); expect(clock.rate).toBeNull();
    const live = clock.hour;
    expect(live).toBeCloseTo(cycleHour(time.now).hour, 9);
    clock.pause();
    expect(clock.local).toBe(true); expect(clock.running).toBe(false);
    advance(60_000);
    expect(clock.hour).toBeCloseTo(live, 9);
    clock.resume(600);
    expect(clock.rate).toBe(600); expect(clock.speed).toBe(600);
    advance(6_000); // ×600: ten clock minutes per real second
    expect(clock.hour).toBeCloseTo(live + 1, 4);
    clock.seek(22);
    advance(3_000);
    expect(clock.hour).toBeCloseTo(22.5, 4);
    clock.step(2);
    expect(clock.hour).toBeCloseTo(0.5, 4); // wraps past midnight and keeps running
    expect(clock.rate).toBe(600);
    clock.pause(); clock.step(-1 / 6);
    expect(formatClock(clock.hour)).toBe('00:20');
    clock.step(-1);
    expect(formatClock(clock.hour)).toBe('23:20');
    advance(10_000);
    expect(formatClock(clock.hour)).toBe('23:20');
    clock.resume(); // the last speed
    expect(clock.rate).toBe(600);
    clock.goLive();
    expect(clock.local).toBe(false); expect(clock.rate).toBeNull();
    expect(clock.hour).toBeCloseTo(cycleHour(time.now).hour, 9);
  });

  test('single-player traffic gets the view\'s hour; a running clock re-sends at most once per real second', () => {
    const { clock, advance } = devClock();
    expect(clock.trafficClock()).toEqual({ kind: 'cycle', periodMs: DAY_MS });
    clock.seek(22);
    expect(clock.trafficClock()).toEqual({ kind: 'hour', hour: 22 });
    for (const [rate, step] of [[600, 10 * MIN], [60, MIN], [1, MIN]] as const) {
      clock.seek(13); clock.resume(rate);
      const sent = new Set<number>();
      for (let i = 0; i < 100; i++) {
        advance(100, 100);
        const traffic = clock.trafficClock();
        if (traffic.kind !== 'hour') throw new Error('not an hour');
        expect(clock.hour - traffic.hour).toBeGreaterThanOrEqual(-1e-9);
        expect(clock.hour - traffic.hour).toBeLessThan(step + 1e-9);
        sent.add(Math.round(traffic.hour * 600));
      }
      expect(sent.size, `×${rate}`).toBeLessThanOrEqual(rate === 1 ? 2 : 11);
    }
    clock.goLive();
    expect(clock.trafficClock()).toEqual({ kind: 'cycle', periodMs: DAY_MS });
  });

  test('hourAt keeps each instant\'s mode, so lives that began before an edit keep their hour', () => {
    const { clock, time, advance } = devClock();
    const shared = (ms: number) => ms; // no server offset
    const before = time.now;
    advance(1_000);
    clock.seek(4);
    const edit = time.now;
    advance(30_000);
    expect(clock.hourAt(shared(before))).toBeCloseTo(cycleHour(before).hour, 9);
    expect(clock.hourAt(shared(edit + 5_000))).toBe(4);
    clock.resume(60);
    const run = time.now;
    advance(60_000);
    expect(clock.hourAt(shared(run + 30_000))).toBeCloseTo(4.5, 4);
    expect(clock.hourAt(shared(edit + 5_000))).toBe(4);
    expect(clock.hourAt(shared(before))).toBeCloseTo(cycleHour(before).hour, 9);
    // Ten minutes later the oldest modes are gone; the last five minutes are still answered.
    for (let i = 0; i < 40; i++) { clock.step(MIN); advance(15_000, 15_000); }
    // A minute ago a step had just re-anchored the ×60 run; since then: three more one-minute steps and four 15 s runs.
    const behind = ((clock.hour - clock.hourAt(shared(time.now - 60_000))) % 24 + 24) % 24;
    expect(behind * 60).toBeCloseTo(3 + 60, 4);
    expect((clock as unknown as { history: unknown[] }).history.length).toBeLessThanOrEqual(22);
  });

  test('a scrub refreshes the sky IBL at most every 150 ms and once it settles; ×600 keeps it within a degree', () => {
    const { clock, env, advance } = devClock();
    clock.pause();
    advance(1_000);
    env.ibl.length = 0;
    // A one-second drag from 13:00 to 22:00, one input event per frame.
    for (let i = 0; i <= 60; i++) { clock.seek(13 + 9 * i / 60); advance(1000 / 60); }
    advance(500);
    expect(env.ibl.length).toBeGreaterThanOrEqual(5); expect(env.ibl.length).toBeLessThanOrEqual(8);
    expect(env.ibl[env.ibl.length - 1]).toBe(env.sunElevation); // the settled sky
    // ×600 through sunset: the IBL follows each degree, no more often than every 250 ms.
    clock.seek(18.5); clock.resume(600);
    env.ibl.length = 0;
    let worst = 0;
    for (let t = 0; t < 12_000; t += 1000 / 60) { advance(1000 / 60); if (env.sunElevation > -12 && env.sunElevation < 10) worst = Math.max(worst, env.skyDrift()); }
    expect(env.ibl.length).toBeGreaterThan(10); expect(env.ibl.length).toBeLessThanOrEqual(12_000 / 250 + 1);
    expect(worst).toBeLessThan(1.5);
  });

  test('the badge and readout name the local view and the live clock; saves only move a developer clock', () => {
    const { clock, time } = devClock();
    const live = formatClock(cycleHour(time.now).hour);
    clock.seek(22);
    expect(clock.badgeText()).toBe(`Local time · paused · live ${live}`);
    expect(clockReadout(clock)).toBe('22:00 · sun −13.4° · night 1.00 · paused');
    clock.resume(600);
    expect(clock.badgeText()).toBe(`Local time ×600 · live ${live}`);
    expect(clockReadout(clock)).toBe('22:00 · sun −13.4° · night 1.00 · ×600');
    clock.restore(new Date(cycleAt(9).time));
    expect(formatClock(clock.hour)).toBe('09:00');
    clock.goLive();
    expect(clockReadout(clock)).toMatch(/ · live$/);
    clock.restore(new Date(cycleAt(9).time));
    expect(clock.local).toBe(false);
    // ?time=22:00 is pinned: a save does not move it, the panel does.
    const pinned = devClock({ kind: 'hour', hour: 22 }).clock;
    expect(pinned.local).toBe(true);
    pinned.restore(new Date(cycleAt(9).time));
    expect(pinned.hour).toBe(22);
    pinned.seek(13);
    expect(pinned.hour).toBe(13);
    pinned.goLive();
    expect(pinned.local).toBe(false);
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
    let money = 120, flying = false, panel = false;
    const { clock } = devClock();
    const hooks: DevHooks = {
      money: () => money, addMoney: euros => { money += euros; calls.push(`money ${euros}`); },
      flying: () => flying, setFlying: on => { flying = on; calls.push(`fly ${on}`); },
      teleport: t => calls.push(`teleport ${t.E},${t.N},${t.headingDeg}`), built: E => E > 23_459_000 && E < 23_459_800,
      ask: () => answer, clock, timePanel: () => panel, toggleTimePanel: () => { panel = !panel; calls.push(`panel ${panel}`); },
      nextTrack: () => calls.push('next track'),
      spawnTruck: () => calls.push('truck'),
    };
    const root: MenuPage = { id: 'pause', title: 'Paused', text: null, back: null, items: [] };
    const flow = new DevMenuFlow(host, hooks, () => root);
    const pick = (page: MenuPage, id: string) => page.items.find(i => i.id === id)!.run();
    return { flow, shown, calls, pick, root, clock, last: () => shown[shown.length - 1] };
  }
  test('money, fly mode, truck spawn, time of day and back', () => {
    const { flow, calls, pick, last, root } = setup();
    const page = flow.root();
    expect(page.items.map(i => i.label)).toEqual(['Money +1 000 €', 'Money +10 000 €', 'Fly mode on',
      'Spawn trophy truck here', 'Teleport to...', 'Time of day', 'Next music track', 'Back']);
    expect(page.text).toBe('Money 120 €.');
    pick(page, 'money-10000');
    expect(last().text).toBe('Money 10 120 €.');
    pick(last(), 'fly');
    expect(last().text).toBe('Fly mode on.');
    expect(last().items.find(i => i.id === 'fly')!.label).toBe('Fly mode off');
    pick(last(), 'truck');
    expect(last().text).toBe('Trophy truck spawned in front of you.');
    pick(last(), 'time');
    expect(last().id).toBe('dev-time');
    expect(calls).toEqual(['money 10000', 'fly true', 'truck']);
    last().back!();
    last().back!();
    expect(last()).toBe(root);
  });
  test('Time of day: the time panel\'s controls as rows that stay selected, with the readout as status', () => {
    const { flow, pick, last, clock, calls } = setup();
    const page = flow.time();
    expect(page.items.map(i => i.label)).toEqual(['Show time panel', 'Pause clock', 'Run at ×1', '−1 h', '+1 h', '−10 min',
      '+10 min', 'Dawn', '09:00', '13:00', 'Golden hour', 'Blue hour', '22:00', '04:00', 'Back to live clock', 'Back']);
    expect(page.items.map(i => i.detail)).toEqual(['T', '0', '×1 · ×10 · ×60 · ×600 (keys 1 2 3 4)', '[', ']', ',', '.', '06:30',
      null, null, '19:30', '20:30', null, null, 'B', null]);
    expect(page.text).toMatch(/^13:00 · sun 35\.5° · night 0\.00 · live$/);
    expect(page.items.find(i => i.id === 'time-live')!.enabled).toBe(false);
    pick(page, 'time-golden');
    expect(last().select).toBe('time-golden');
    expect(last().text).toBe('19:30 · sun 3.4° · night 0.10 · paused');
    expect(last().items.find(i => i.id === 'time-pause')!.label).toBe('Resume clock ×60');
    pick(last(), 'time-hourforward'); pick(last(), 'time-minutesback');
    expect(formatClock(clock.hour)).toBe('20:20');
    expect(last().select).toBe('time-minutesback');
    pick(last(), 'time-speed');
    expect(clock.rate).toBe(1);
    expect(last().items.find(i => i.id === 'time-speed')!.label).toBe('Run at ×10');
    for (const speed of [10, 60, 600, 1]) { pick(last(), 'time-speed'); expect(clock.rate).toBe(speed); }
    expect(last().text).toMatch(/ · ×1$/);
    pick(last(), 'time-pause');
    expect(clock.running).toBe(false);
    pick(last(), 'time-panel');
    expect(last().items[0].label).toBe('Hide time panel');
    expect(last().items.find(i => i.id === 'time-live')!.enabled).toBe(true);
    pick(last(), 'time-live');
    expect(clock.local).toBe(false);
    expect(last().text).toMatch(/ · live$/);
    expect(calls).toEqual(['panel true']);
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
    const hooks: GameHooks = { capture: () => { throw new Error('unused'); }, play: () => {}, resume: () => {}, quit: () => {},
      respawn: () => {} };
    const host = { show: () => {} };
    let opened = 0;
    const withDev = new SaveMenuFlow(host, store, hooks, undefined, undefined, () => { opened++; }).pause();
    expect(withDev.items.map(i => i.id)).toEqual(['resume', 'respawn', 'save', 'load', 'new', 'dev', 'quit']);
    withDev.items.find(i => i.id === 'dev')!.run();
    expect(opened).toBe(1);
    expect(new SaveMenuFlow(host, store, hooks).pause().items.map(i => i.id)).not.toContain('dev');
  });
});
