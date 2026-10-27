import { describe, expect, it } from 'vitest';
import {
  Autosave, LEGACY_MISSION_KEY, SAVES_KEY, SaveStore, formatDetails, formatLabel, type KeyValueStore, type SaveGame,
} from '../../src/core/save';
import { SaveMenuFlow, type GameHooks, type MenuPage } from '../../src/ui/menu';
import { MissionSystem, missionSummary } from '../../src/gameplay/missions/missions';
import { kerbBehind, queuesIntro } from '../../src/gameplay/saveGame';
import { PERMIT_DELIVERIES } from '../../src/gameplay/economy';
import type { Router } from '../../src/gameplay/contract';

class MemoryStore implements KeyValueStore {
  data = new Map<string, string>();
  getItem(key: string) { return this.data.get(key) ?? null; }
  setItem(key: string, value: string) { this.data.set(key, value); }
  removeItem(key: string) { this.data.delete(key); }
}

const router: Router = { route: (from, to) => [from, to], distance: p => Math.hypot(p[1].x - p[0].x, p[1].z - p[0].z) };

function game(savedAt: number, extra: Partial<SaveGame> = {}): SaveGame {
  return { version: 1, savedAt, label: `Game ${savedAt}`, mission: '{}', player: { E: 23459668, N: 6704254, H: 2.5, yaw: 1 },
    vehicle: { owned: true, E: 23459670, N: 6704250, H: 2.4, yaw: 0.5 }, clock: { timeOfDay: '2026-06-21T09:30:00.000Z' },
    stats: { deliveries: 3, money: 140 }, ...extra };
}

describe('save slots', () => {
  it('writes, reads, lists and removes slots under one storage key', () => {
    const storage = new MemoryStore();
    const store = new SaveStore(storage, missionSummary);
    expect(store.any).toBe(false);
    expect(store.latest()).toBeNull();
    store.write('slot2', game(200));
    store.write('auto', game(300));
    store.write('slot1', game(100));
    expect([...storage.data.keys()]).toEqual([SAVES_KEY]);
    expect(store.latest()).toEqual({ id: 'auto', game: game(300) });
    const reopened = new SaveStore(storage, missionSummary);
    expect(reopened.get('slot2')).toEqual(game(200));
    expect(reopened.has('slot3')).toBe(false);
    reopened.remove('auto');
    expect(new SaveStore(storage, missionSummary).latest()?.id).toBe('slot2');
  });

  it('drops malformed slots and survives corrupt or blocked storage', () => {
    const storage = new MemoryStore();
    storage.setItem(SAVES_KEY, JSON.stringify({ version: 1, slots: {
      slot1: game(1), slot2: { ...game(2), player: { E: 'x' } }, slot3: { ...game(3), clock: { timeOfDay: 'soon' } }, other: game(4),
    } }));
    const store = new SaveStore(storage, missionSummary);
    expect([store.has('slot1'), store.has('slot2'), store.has('slot3')]).toEqual([true, false, false]);
    storage.setItem(SAVES_KEY, 'not json');
    expect(new SaveStore(storage, missionSummary).any).toBe(false);
    const full: KeyValueStore = { getItem: () => null, setItem: () => { throw new Error('QuotaExceededError'); }, removeItem: () => {} };
    const memoryOnly = new SaveStore(full, missionSummary);
    memoryOnly.write('slot1', game(5));
    expect(memoryOnly.get('slot1')).toEqual(game(5));
    const none = new SaveStore(null, missionSummary);
    none.write('auto', game(6));
    expect(none.latest()?.id).toBe('auto');
  });

  it('turns the unslotted mission save of main into slot 1 on the first run', () => {
    const missions = new MissionSystem({ router });
    missions.begin();
    const legacy = JSON.stringify({ version: 2, phase: 'at_pizzeria', money: 57, permit: PERMIT_DELIVERIES, plates: false,
      kidney: true, warning: false, vehicle: 'none', delivery: 2, elapsed: 0, par: 0 });
    const storage = new MemoryStore();
    storage.setItem(LEGACY_MISSION_KEY, legacy);
    const store = new SaveStore(storage, missionSummary);
    const slot = store.get('slot1')!;
    expect(slot).toMatchObject({ mission: legacy, player: null, vehicle: null, clock: null, stats: { deliveries: 2, money: 57 } });
    expect(slot.label).toMatch(/^.+ · \d\d:\d\d$/);
    expect(storage.getItem(LEGACY_MISSION_KEY)).toBeNull();
    expect(JSON.parse(storage.getItem(SAVES_KEY)!).slots.slot1.mission).toBe(legacy);
    missions.restore(slot.mission);
    expect(missions).toMatchObject({ money: 57, permit: PERMIT_DELIVERIES, kidney: true, deliveries: 2 });
    // A slot 1 that already exists is never replaced by a stale legacy key.
    storage.setItem(LEGACY_MISSION_KEY, '{"version":2}');
    expect(new SaveStore(storage, missionSummary).get('slot1')!.mission).toBe(legacy);
    expect(storage.getItem(LEGACY_MISSION_KEY)).toBeNull();
  });

  it('labels a slot with the last delivery address and the Helsinki clock time', () => {
    expect(formatLabel('Linnankatu 21', new Date('2026-06-21T09:30:00Z'))).toBe('Linnankatu 21 · 12:30');
    expect(formatLabel(null, new Date('2026-01-10T22:05:00Z'))).toBe('Before the first delivery · 00:05');
    const details = formatDetails(game(Date.UTC(2026, 9, 27, 10, 0)), 'en-GB');
    expect(details).toMatch(/^3 deliveries · 140 € · saved 27\/10\/2026, \d\d:00$/);
    expect(formatDetails(game(0, { stats: { deliveries: 1, money: 0 } }))).toMatch(/^1 delivery · 0 € · saved /);
  });
});

describe('autosave', () => {
  it('writes the autosave slot while a game runs and records why', () => {
    const store = new SaveStore(new MemoryStore(), missionSummary);
    let running: SaveGame | null = null;
    const autosave = new Autosave(store, () => running);
    expect(autosave.save('delivery')).toBe(false);
    expect(store.any).toBe(false);
    running = game(10);
    expect(autosave.save('purchase')).toBe(true);
    expect(store.get('auto')).toEqual(game(10));
    expect(autosave.last).toBe('purchase');
  });

  it('saves on page hide and on a hidden tab until detached', () => {
    const store = new SaveStore(new MemoryStore(), missionSummary);
    let n = 0;
    const autosave = new Autosave(store, () => game(++n));
    const win = new EventTarget(), doc = Object.assign(new EventTarget(), { visibilityState: 'visible' as DocumentVisibilityState });
    const detach = autosave.attach(win as unknown as Window, doc as unknown as Document);
    doc.dispatchEvent(new Event('visibilitychange'));
    expect(store.any).toBe(false);
    win.dispatchEvent(new Event('pagehide'));
    expect(store.get('auto')?.savedAt).toBe(1);
    doc.visibilityState = 'hidden';
    doc.dispatchEvent(new Event('visibilitychange'));
    expect(store.get('auto')?.savedAt).toBe(2);
    expect(autosave.last).toBe('pagehide');
    detach();
    win.dispatchEvent(new Event('pagehide'));
    expect(store.get('auto')?.savedAt).toBe(2);
  });
});

describe('save menus', () => {
  function flow(store = new SaveStore(new MemoryStore(), missionSummary)) {
    const shown: MenuPage[] = [];
    const played: (SaveGame | null)[] = [];
    const calls: string[] = [];
    let captured = 0;
    const hooks: GameHooks = {
      capture: () => game(1000 + ++captured), play: g => { played.push(g); }, resume: () => { calls.push('resume'); },
      quit: () => { calls.push('quit'); }, respawn: () => { calls.push('respawn'); },
    };
    const host = { show: (page: MenuPage) => { shown.push(page); } };
    const menus = new SaveMenuFlow(host, store, hooks);
    const page = () => shown[shown.length - 1];
    const pick = (p: MenuPage, id: string) => {
      const item = p.items.find(i => i.id === id)!;
      expect(item.enabled, `${p.id}/${id} enabled`).toBe(true);
      item.run();
    };
    return { store, menus, shown, played, calls, page, pick };
  }
  const ids = (p: MenuPage) => p.items.map(i => `${i.id}${i.enabled ? '' : '-'}`);

  it('starts a first game at once: no Continue, Load greyed', () => {
    const f = flow();
    const start = f.menus.start();
    expect(ids(start)).toEqual(['new', 'load-']);
    f.pick(start, 'new');
    expect(f.played).toEqual([null]);
    expect(f.played.map(queuesIntro)).toEqual([true]);
    expect(f.shown).toHaveLength(0);
  });

  it('continues the latest save with its details and confirms a new game when saves exist', () => {
    const f = flow();
    f.store.write('slot3', game(50));
    f.store.write('auto', game(40));
    const start = f.menus.start();
    expect(ids(start)).toEqual(['continue', 'new', 'load']);
    expect(start.items[0].detail).toMatch(/^Game 50 · 3 deliveries · 140 € · saved /);
    f.pick(start, 'continue');
    expect(f.played).toEqual([game(50)]);
    f.pick(start, 'new');
    expect(f.page()).toMatchObject({ id: 'confirm-new' });
    f.pick(f.page(), 'no');
    expect(f.page().id).toBe('start');
    f.pick(start, 'new');
    f.pick(f.page(), 'yes');
    expect(f.played).toEqual([game(50), null]);
    expect(f.played.map(queuesIntro)).toEqual([false, true]);
  });

  it('lists the autosave and three slots for loading, empty slots greyed, back to the caller', () => {
    const f = flow();
    f.store.write('slot2', game(20));
    f.pick(f.menus.start(), 'load');
    const load = f.page();
    expect(ids(load)).toEqual(['auto-', 'slot1-', 'slot2', 'slot3-', 'back']);
    expect(load.items[2]).toMatchObject({ label: 'Slot 2 · Game 20' });
    expect(load.items[0]).toMatchObject({ label: 'Autosave · Empty', detail: null });
    f.pick(load, 'slot2');
    expect(f.played).toEqual([game(20)]);
    expect(f.played.map(queuesIntro)).toEqual([false]);
    load.back!();
    expect(f.page().id).toBe('start');
  });

  it('saves into an empty slot at once and asks before overwriting a filled one', () => {
    const f = flow();
    const pause = f.menus.pause();
    expect(ids(pause)).toEqual(['resume', 'respawn', 'save', 'load-', 'new', 'quit']);
    f.pick(pause, 'save');
    expect(ids(f.page())).toEqual(['slot1', 'slot2', 'slot3', 'back']);
    f.pick(f.page(), 'slot2');
    expect(f.store.get('slot2')?.savedAt).toBe(1001);
    expect(f.page()).toMatchObject({ id: 'pause', text: 'Saved to Slot 2.' });
    f.pick(f.page(), 'save');
    f.pick(f.page(), 'slot2');
    expect(f.page()).toMatchObject({ id: 'confirm-overwrite', text: 'Overwrite Slot 2 (Game 1001)?' });
    f.pick(f.page(), 'no');
    expect(f.store.get('slot2')?.savedAt).toBe(1001);
    f.pick(f.page(), 'slot2');
    f.pick(f.page(), 'yes');
    expect(f.store.get('slot2')?.savedAt).toBe(1002);
    expect(f.page().text).toBe('Saved to Slot 2.');
  });

  it('pause menu: back resumes, new game always confirms, quit leaves', () => {
    const f = flow();
    const pause = f.menus.pause();
    pause.back!();
    f.pick(pause, 'quit');
    expect(f.calls).toEqual(['resume', 'quit']);
    f.pick(pause, 'new');
    expect(f.page().id).toBe('confirm-new');
    f.page().back!();
    expect(f.page().id).toBe('pause');
    f.pick(pause, 'new');
    f.pick(f.page(), 'yes');
    expect(f.played).toEqual([null]);
  });

  it('respawn at the start asks first; Cancel and back return to the pause menu, the game is not reloaded', () => {
    const f = flow();
    const pause = f.menus.pause();
    f.pick(pause, 'respawn');
    expect(f.page()).toMatchObject({ id: 'confirm-respawn', text: 'Respawn? You keep your money and order.' });
    expect(f.page().items.map(i => i.label)).toEqual(['Respawn', 'Cancel']);
    f.pick(f.page(), 'no');
    expect(f.page().id).toBe('pause');
    f.pick(pause, 'respawn');
    f.page().back!();
    expect(f.page().id).toBe('pause');
    expect(f.calls).toEqual([]);
    f.pick(pause, 'respawn');
    f.pick(f.page(), 'yes');
    expect(f.calls).toEqual(['respawn']);
    expect(f.played).toEqual([]);
  });

  it('a driven vehicle other than the own car waits at the kerb 5.5 m behind the start car', () => {
    const car = { x: 10, y: 2, z: 20, yaw: Math.PI / 2 }; // chassis +X points to -Z (north)
    const behind = kerbBehind(car);
    expect(behind.x).toBeCloseTo(10);
    expect(behind.z).toBeCloseTo(25.5);
    expect(behind).toMatchObject({ y: 2, yaw: Math.PI / 2 });
  });

});
