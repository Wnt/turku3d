import { describe, expect, it, vi } from 'vitest';
import { SAVES_KEY, SaveStore, type KeyValueStore, type SaveGame } from '../../src/core/save';
import { SaveMenuFlow, type GameHooks, type MenuPage } from '../../src/ui/menu';
import { missionSummary } from '../../src/gameplay/missions/missions';
import { clearProgress } from '../../src/gameplay/progressReset';
import { MultiplayerClient } from '../../src/net/client';
import { SharedVehicles, vehiclePose } from '../../src/net/sharedVehicles';
import type { PhysicsClient } from '../../src/physics/client';
import type { VehicleState } from '../../src/physics/protocol';
import type { Manifest } from '../../src/world/manifest';
import type { ParkedVehicles } from '../../src/world/parkedVehicles';

class MemoryStore implements KeyValueStore {
  data = new Map<string, string>();
  getItem(key: string) { return this.data.get(key) ?? null; }
  setItem(key: string, value: string) { this.data.set(key, value); }
  removeItem(key: string) { this.data.delete(key); }
}

const game = (savedAt: number): SaveGame => ({ version: 1, savedAt, label: `Game ${savedAt}`, mission: '{}',
  player: { E: 23459668, N: 6704254, H: 2.5, yaw: 1 }, vehicle: null, clock: null, stats: { deliveries: 3, money: 140 } });

describe('pause menu: Reset progress', () => {
  function flow(multiplayer = false, reset = true) {
    const shown: MenuPage[] = [];
    const calls: string[] = [];
    const hooks: GameHooks = { capture: () => game(1), play: () => { calls.push('play'); }, resume: () => { calls.push('resume'); },
      quit: () => { calls.push('quit'); }, respawn: () => { calls.push('respawn'); },
      ...(reset ? { resetProgress: () => { calls.push('reset'); } } : {}) };
    const menus = new SaveMenuFlow({ show: page => { shown.push(page); } }, new SaveStore(new MemoryStore(), missionSummary), hooks,
      undefined, undefined, undefined, undefined, undefined, () => multiplayer);
    return { menus, calls, page: () => shown[shown.length - 1] };
  }
  const pick = (page: MenuPage, id: string) => page.items.find(i => i.id === id)!.run();

  it('sits before Quit in solo and multiplayer, only where the session can reset', () => {
    expect(flow().menus.pause().items.map(i => i.id)).toEqual(['resume', 'respawn', 'save', 'load', 'new', 'reset-progress', 'quit']);
    expect(flow(true).menus.pause().items.map(i => i.id)).toEqual(['resume', 'respawn', 'reset-progress', 'quit']);
    expect(flow(false, false).menus.pause().items.map(i => i.id)).not.toContain('reset-progress');
  });

  it('asks first with Cancel selected, says what is lost, and only Reset progress resets', () => {
    const f = flow();
    pick(f.menus.pause(), 'reset-progress');
    const confirm = f.page();
    expect(confirm).toMatchObject({ id: 'confirm-reset', title: 'Reset progress', select: 'no' });
    expect(confirm.items.map(i => [i.id, i.label])).toEqual([['no', 'Cancel'], ['yes', 'Reset progress']]);
    expect(confirm.text).toBe('Start over as a new player? Your money, deliveries, missions, vehicles and saved games are deleted. '
      + 'You begin again at the office in Turku. Settings are kept.');
    // Cancel and back return to the pause menu on the same row; nothing is reset.
    pick(confirm, 'no');
    expect(f.page()).toMatchObject({ id: 'pause', select: 'reset-progress' });
    confirm.back!();
    expect(f.page()).toMatchObject({ id: 'pause', select: 'reset-progress' });
    expect(f.calls).toEqual([]);
    pick(confirm, 'yes');
    expect(f.calls).toEqual(['reset']);
  });

  it('in a room also names the score and keeps the nickname', () => {
    const f = flow(true);
    pick(f.menus.pause(), 'reset-progress');
    expect(f.page().text).toBe('Start over as a new player? Your money, deliveries, missions, vehicles and saved games are deleted '
      + 'and your score this hour goes to zero. You begin again at the office in Turku. Settings and your nickname are kept.');
  });
});

describe('stored progress', () => {
  it('clears every city\'s saves, multiplayer progress and trip state; settings and identity stay; rooms are marked', () => {
    const local = new MemoryStore(), session = new MemoryStore();
    const settings = { 'turku3d.audio': '{"volumes":{}}', 'turku3d.controls-help.v1': '{}', 'turku3d.devLayers': '{}' };
    for (const [k, v] of Object.entries(settings)) local.setItem(k, v);
    for (const k of [SAVES_KEY, `${SAVES_KEY}.espoo`, `${SAVES_KEY}.amsterdam`, 'turku3d.missions']) local.setItem(k, '{}');
    for (const k of ['turku3d.mp.progress.v1', 'turku3d.mp.progress.v1.espoo', 'turku3d.travel.v1', 'turku3d.travel.portal.v1', 'turku3d.resume'])
      session.setItem(k, '{}');
    session.setItem('turku3d.mp.session.v1', 'a'.repeat(64));
    session.setItem('turku3d.mp.session.v1.espoo', 'b'.repeat(64));
    clearProgress(local, session);
    expect(Object.fromEntries(local.data)).toEqual(settings);
    expect(Object.fromEntries(session.data)).toEqual({ 'turku3d.mp.session.v1': 'a'.repeat(64), 'turku3d.mp.session.v1.espoo': 'b'.repeat(64),
      'turku3d.mp.reset.v1': '1', 'turku3d.mp.reset.v1.espoo': '1' });
    expect(() => clearProgress(null, null)).not.toThrow();
  });

  it('a cleared save store forgets every slot, also for a later autosave', () => {
    const storage = new MemoryStore();
    const store = new SaveStore(storage, missionSummary);
    store.write('slot1', game(10)); store.write('auto', game(20));
    store.clear();
    expect([store.any, storage.getItem(SAVES_KEY)]).toEqual([false, null]);
    store.write('auto', game(30));
    expect(new SaveStore(storage, missionSummary).latest()).toEqual({ id: 'auto', game: game(30) });
    expect(new SaveStore(storage, missionSummary).has('slot1')).toBe(false);
  });
});

class Socket extends EventTarget {
  static OPEN = 1;
  static last: Socket;
  readyState = 1;
  sent: Record<string, unknown>[] = [];
  constructor() { super(); Socket.last = this; }
  send(data: string) { this.sent.push(JSON.parse(data)); }
  close() { this.readyState = 3; this.dispatchEvent(new Event('close')); }
  message(data: object) { this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(data) })); }
}
const welcome = { v: 2, type: 'welcome', session: 'a'.repeat(64), you: { id: 'self', nick: 'AA', spawn: { E: 0, N: 0, yaw: 0 } },
  hour: {}, players: [], scores: [], vehicles: { upsert: [], remove: [], sources: [] }, serverTime: 0 };

describe('room reset (net/client.ts)', () => {
  it('sends reset after a queued release, resolves on the answer; offline, the next hello carries it', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
    vi.stubGlobal('WebSocket', Socket);
    const session = new MemoryStore();
    try {
      const client = new MultiplayerClient('ws://localhost:5403/', session as unknown as Storage);
      client.connect();
      Socket.last.dispatchEvent(new Event('open'));
      expect(Socket.last.sent[0]).toEqual({ v: 2, type: 'hello' });
      Socket.last.message(welcome);
      void client.vehicle({ action: 'release', id: 'self:player' });
      const answered = client.resetProgress();
      expect(session.getItem('turku3d.mp.reset.v1')).toBe('1');
      await vi.advanceTimersByTimeAsync(200);
      expect(Socket.last.sent.slice(1).map(m => [m.type, m.action])).toEqual([['vehicle', 'release'], ['reset', undefined]]);
      Socket.last.message({ v: 2, type: 'progress_reset' });
      await expect(answered).resolves.toBe(true);
      expect(session.getItem('turku3d.mp.reset.v1')).toBeNull();

      // A dropped socket answers false at once; the reconnect's hello carries the reset and its welcome confirms it.
      const pending = client.resetProgress();
      Socket.last.close();
      await expect(pending).resolves.toBe(false);
      await expect(client.resetProgress()).resolves.toBe(false);
      await vi.advanceTimersByTimeAsync(1000);
      Socket.last.dispatchEvent(new Event('open'));
      expect(Socket.last.sent).toEqual([{ v: 2, type: 'hello', session: 'a'.repeat(64), reset: true }]);
      Socket.last.message(welcome);
      expect(session.getItem('turku3d.mp.reset.v1')).toBeNull();
      await vi.advanceTimersByTimeAsync(200);
      expect(Socket.last.sent.map(m => m.type)).toEqual(['hello']); // the dropped reset is not sent again
      client.disconnect();
    } finally {
      vi.useRealTimers();
      vi.unstubAllGlobals();
    }
  });
});

describe('shared vehicles resync (net/sharedVehicles.ts)', () => {
  it('forgets the shared records and their bodies but keeps the start car for the new game', () => {
    const client = new MultiplayerClient('ws://localhost:5403', null);
    client.state.connected = true; client.state.you = { id: 'self', nick: 'AA', spawn: { E: 170, N: -250, yaw: 0 } };
    const car: VehicleState = { id: 'player', kind: 'car', pos: { x: 170, y: 4.6, z: 250 }, rot: { x: 0, y: 0, z: 0, w: 1 },
      linvel: { x: 0, y: 0, z: 0 }, speed: 0, forwardSpeed: 0, wheels: [], curbStrike: null };
    const scooter: VehicleState = { ...car, id: 'scooter', kind: 'scooter' };
    const physics = { latest: { vehicles: [car, scooter] }, spawnVehicle: vi.fn(), removeVehicle: vi.fn(), vehicleImpulse: vi.fn(), setVehicleDamage: vi.fn(), ownVehicleDamage: vi.fn() };
    const parked = { setShared: vi.fn(), resetRound: vi.fn() };
    const shared = new SharedVehicles(client, physics as unknown as PhysicsClient, { vehicles: [] } as unknown as Manifest,
      parked as unknown as ParkedVehicles);
    for (const v of [car, scooter]) client.state.vehicles.set(`self:${v.id}`, { id: `self:${v.id}`, creator: 'self', localId: v.id!,
      source: null, owner: 'self', occupant: null, epoch: 1, revision: 1, half: { x: 1, y: .3, z: .5 }, pose: vehiclePose(v, v.id!, 1) });
    shared.update(10);
    shared.resetRound();
    expect(physics.removeVehicle.mock.calls).toEqual([['scooter']]);
    expect(parked.resetRound).toHaveBeenCalledOnce();
  });
});
