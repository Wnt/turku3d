import { afterEach, expect, test, vi } from 'vitest';
import { MultiplayerClient } from '../../src/net/client';
import { prepareMultiplayer } from '../../src/net/preparedStart';
import { SharedVehicles, vehiclePose } from '../../src/net/sharedVehicles';
import { SaveGameController } from '../../src/gameplay/saveGame';
import { multiplayerStart } from '../../src/gameplay/startPoints';
import { SaveStore } from '../../src/core/save';
import { missionSummary } from '../../src/gameplay/missions/missions';
import type { SharedVehicle } from '../../src/net/vehicleTypes';
import type { VehicleState } from '../../src/physics/protocol';
import type { VehicleKind } from '../../src/physics/constants';
import type { PhysicsClient } from '../../src/physics/client';
import type { Manifest } from '../../src/world/manifest';
import type { ParkedVehicles } from '../../src/world/parkedVehicles';

class Socket extends EventTarget {
  static OPEN = 1;
  static current: Socket;
  readyState = 1;
  sent: Record<string, unknown>[] = [];
  constructor() { super(); Socket.current = this; }
  send(data: string) { this.sent.push(JSON.parse(data)); }
  close() { this.readyState = 3; this.dispatchEvent(new Event('close')); }
  welcome(vehicles: SharedVehicle[]) {
    this.dispatchEvent(new Event('open'));
    this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify({ v: 2, type: 'welcome',
      session: 'a'.repeat(64), you: { id: 'self', nick: 'AA', spawn: { E: 170, N: -250, yaw: 0 } },
      hour: {}, players: [{ id: 'self', nick: 'AA', pose: { E: 715, N: -800, H: 4, yaw: 0 } }], scores: [], serverTime: 0,
      vehicles: { upsert: vehicles, remove: [], sources: vehicles.flatMap(v => v.source ? [v.source] : []) } }) }));
  }
}

const clients: MultiplayerClient[] = [];
afterEach(() => {
  clients.splice(0).forEach(c => c.disconnect());
  vi.useRealTimers(); vi.unstubAllGlobals();
});

function setup(kind: VehicleKind, seated: boolean, source: string | null = null) {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
  vi.stubGlobal('WebSocket', Socket);
  vi.stubGlobal('window', new EventTarget());
  vi.stubGlobal('document', new EventTarget());
  vi.stubGlobal('location', { hostname: 'localhost', search: '' });
  const values = new Map<string, string>();
  const storage: Storage = { get length() { return values.size; }, key: i => [...values.keys()][i] ?? null,
    getItem: k => values.get(k) ?? null, setItem: (k, v) => { values.set(k, v); },
    removeItem: k => { values.delete(k); }, clear: () => values.clear() };
  Object.assign(window, { sessionStorage: storage });
  const localId = source ?? (kind === 'car' ? 'player' : kind);
  const state: VehicleState = { id: localId, kind, pos: { x: 720, y: 4.6, z: 800 },
    rot: { x: .6, y: 0, z: 0, w: .8 }, linvel: { x: 0, y: 0, z: 0 },
    speed: 0, forwardSpeed: 0, wheels: [], curbStrike: null };
  const vehicle: SharedVehicle = { id: source ? `park:${source}` : `self:${localId}`, localId, source,
    creator: 'self', owner: 'self', occupant: seated ? 'self' : null, epoch: 2, revision: 3,
    half: { x: 2, y: .5, z: 1 }, pose: vehiclePose(state, source ? 'sedan/red' : kind === 'car' ? 'player' : kind, 10) };
  const first = new MultiplayerClient('ws://localhost:5573', storage);
  clients.push(first); first.connect(); Socket.current.welcome([vehicle]); first.disconnect();
  // A reload constructs both classes from scratch, retaining only browser storage.
  const { client, ready } = prepareMultiplayer();
  clients.push(client); client.connect(); Socket.current.welcome([vehicle]);
  expect(Socket.current.sent[0]).toMatchObject({ type: 'hello', session: 'a'.repeat(64) });
  const latest = { vehicles: [] as VehicleState[], character: { pos: { x: 720, y: 4, z: 800 } } };
  const physics = { latest, loaded: new Map(), step: vi.fn(),
    resetVehicle: vi.fn((pos, _yaw, id) => { latest.vehicles = [{ ...state, id, pos }]; }),
    spawnVehicle: vi.fn((pos, _yaw, spec) => {
      latest.vehicles = [...latest.vehicles.filter(s => s.id !== spec.id),
        { ...state, id: spec.id, pos, rot: spec.rot, linvel: spec.linvel }];
    }),
    removeVehicle: vi.fn(), driveVehicle: vi.fn(), setCharacterEnabled: vi.fn((enabled, pos) => {
      if (enabled && pos) latest.character.pos = pos;
    }),
    raycast: vi.fn(async () => ({ point: { y: 4 } })),
  };
  const manifest = { vehicles: [] } as unknown as Manifest;
  const shared = new SharedVehicles(client, physics as unknown as PhysicsClient, manifest,
    { setShared: vi.fn() } as unknown as ParkedVehicles);
  const enterVehicle = vi.fn((id: string) => shared.drive(id));
  const paused = vi.fn();
  const missions = { vehicle: 'none', restore: vi.fn(), begin: vi.fn() };
  const save = new SaveGameController({
    physics, streamer: { update: vi.fn() }, manifest: { collision: {} },
    missions, recovery: { restart: vi.fn() },
    saves: new SaveStore(null, missionSummary), startScreen: { close: vi.fn() }, pauseMenu: { close: vi.fn() },
    car: { x: 180, y: 4, z: 260, yaw: 0 }, spawn: { x: 170, y: 4, z: 250, yaw: 0 },
    view: () => ({ mode: 'foot' }), placed: vi.fn(), enterVehicle, setPaused: paused,
  } as unknown as ConstructorParameters<typeof SaveGameController>[0]);
  const at = multiplayerStart(client.state.you!, { pose: vehicle.pose }, seated ? vehicle : undefined);
  return { client, shared, save, at, ready, vehicle, state, manifest, physics, enterVehicle, paused, missions };
}

test.each(['car', 'scooter', 'truck'] as const)('tab reload while driving %s restores the same body, pose and seat before unpausing', async kind => {
  const h = setup(kind, true);
  expect(await h.ready).toEqual(h.at);
  await h.save.playAt((await h.ready)!, () => h.shared.restore());
  const id = h.vehicle.localId;
  expect(h.enterVehicle).toHaveBeenCalledWith(id);
  expect(h.physics.driveVehicle).toHaveBeenCalledWith(id);
  expect(h.physics.setCharacterEnabled).toHaveBeenLastCalledWith(false);
  expect(h.physics.spawnVehicle).toHaveBeenLastCalledWith(h.state.pos, expect.any(Number), expect.objectContaining({ id, rot: h.state.rot }));
  expect(h.shared.hasSeat(id)).toBe(true);
  expect(h.save.loading).toBe(false);
  expect(h.paused.mock.calls).toEqual([[true], [false]]);
  h.shared.update(0);
  await vi.advanceTimersByTimeAsync(1100);
  h.shared.update(1100);
  expect(h.physics.removeVehicle).not.toHaveBeenCalled();
  expect(h.shared.seatExit(1100)).toBeNull();
  expect(h.physics.latest.vehicles.find(v => v.id === id)?.pos).toEqual(h.state.pos);
  h.save.dispose();
});

test.each(['car', 'scooter', 'truck'] as const)('reload beside a parked %s preserves its pose and stays on foot', async kind => {
  const h = setup(kind, false);
  await h.save.playAt((await h.ready)!, () => h.shared.restore());
  h.shared.update(0);
  await vi.advanceTimersByTimeAsync(1100);
  h.shared.update(1100);
  expect(h.physics.latest.vehicles.find(v => v.id === h.vehicle.localId)?.pos).toEqual(h.state.pos);
  expect(h.enterVehicle).not.toHaveBeenCalled();
  expect(h.physics.driveVehicle).not.toHaveBeenCalled();
  expect(h.physics.removeVehicle).not.toHaveBeenCalled();
  h.save.dispose();
});

test('reload in a borrowed car without its source tile uses the shared actor and renderer', async () => {
  const h = setup('car', true, 'distant-tile:bay');
  await h.save.playAt((await h.ready)!, () => h.shared.restore());
  expect(h.enterVehicle).toHaveBeenCalledWith(h.vehicle.id);
  expect(h.shared.localId(h.vehicle)).toBe(h.vehicle.id);
  expect(h.shared.hasSeat(h.vehicle.id)).toBe(true);
  h.shared.update(1100);
  expect(h.physics.removeVehicle).not.toHaveBeenCalled();
  h.manifest.vehicles = [{ object_id: h.vehicle.source! }] as Manifest['vehicles'];
  h.shared.release(h.vehicle.id);
  h.shared.sync();
  expect(h.shared.localId(h.vehicle)).toBe(h.vehicle.id);
  expect(h.physics.latest.vehicles.filter(v => v.id === h.vehicle.id)).toHaveLength(1);
  h.save.dispose();
});

test('multiplayer remains paused until the worker publishes the restored body', async () => {
  const h = setup('car', true);
  const spawn = h.physics.spawnVehicle.getMockImplementation()!;
  let pending: (() => void) | null = null;
  h.physics.spawnVehicle.mockImplementation((pos, yaw, spec) => { pending = () => spawn(pos, yaw, spec); });
  h.physics.step.mockImplementation(() => { pending?.(); pending = null; });
  const loading = h.save.playAt((await h.ready)!, () => h.shared.restore());
  await vi.advanceTimersByTimeAsync(0);
  expect(h.save.loading).toBe(true);
  expect(h.enterVehicle).not.toHaveBeenCalled();
  expect(h.paused).not.toHaveBeenCalledWith(false);
  await vi.advanceTimersByTimeAsync(20);
  await loading;
  expect(h.enterVehicle).toHaveBeenCalledWith('player');
  expect(h.save.loading).toBe(false);
  h.save.dispose();
});

test('joining again from the menu restores authority even when its epoch has not changed', async () => {
  const h = setup('car', false);
  const at = (await h.ready)!;
  await h.save.playAt(at, () => h.shared.restore());
  h.shared.stop(null);
  h.physics.spawnVehicle.mockClear();
  await h.save.playAt(at, () => h.shared.restore());
  expect(h.physics.spawnVehicle).toHaveBeenCalledWith(h.state.pos, expect.any(Number), expect.objectContaining({ id: 'player' }));
  expect(h.physics.latest.vehicles.find(v => v.id === 'player')?.pos).toEqual(h.state.pos);
  h.save.dispose();
});

test('multiplayer rejoin restores the saved mission but keeps the server vehicle and position', async () => {
  const h = setup('car', true);
  const mission = JSON.stringify({ version: 4, money: 111, permit: 3, plates: true });
  await h.save.playAt((await h.ready)!, () => h.shared.restore(), mission);
  expect(h.missions.restore).toHaveBeenCalledWith(mission);
  expect(h.physics.latest.character.pos).toMatchObject({ x: 720, z: 800 });
  expect(h.physics.latest.vehicles.find(v => v.id === 'player')?.pos).toEqual(h.state.pos);
  expect(h.enterVehicle).toHaveBeenCalledWith('player');
  h.save.dispose();
});
