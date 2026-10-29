import { afterEach, expect, test, vi } from 'vitest';
import { MultiplayerClient } from '../../src/net/client';
import { SharedVehicles, SEAT_CONFLICT_GRACE_MS, vehiclePose } from '../../src/net/sharedVehicles';
import { driverSide } from '../../src/gameplay/saveGame';
import type { SharedVehicle } from '../../src/net/vehicleTypes';
import type { VehicleState } from '../../src/physics/protocol';
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
  message(message: object) { this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify({ v: 2, ...message }) })); }
}

const clients: MultiplayerClient[] = [];
afterEach(() => {
  for (const c of clients.splice(0)) c.disconnect();
  vi.useRealTimers(); vi.unstubAllGlobals();
});

function setup() {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
  vi.stubGlobal('WebSocket', Socket);
  const client = new MultiplayerClient('ws://localhost:5573', null);
  clients.push(client);
  client.connect();
  const state: VehicleState = { id: 'truck', kind: 'truck', pos: { x: 174.7, y: 4.4, z: 256.4 },
    rot: { x: 0, y: 0, z: 0, w: 1 }, linvel: { x: 0, y: 0, z: 0 },
    speed: 0, forwardSpeed: 0, wheels: [], curbStrike: null };
  const vehicle: SharedVehicle = { id: 'self:truck', source: null, creator: 'self', localId: 'truck',
    owner: 'self', occupant: 'self', epoch: 1, revision: 1, half: { x: 2.8, y: .6, z: 1.1 }, pose: vehiclePose(state, 'truck', 0) };
  const welcome = (vehicles = [vehicle], id = 'self') => {
    Socket.current.dispatchEvent(new Event('open'));
    Socket.current.message({ type: 'welcome', session: 'a'.repeat(64), you: { id, nick: 'AA', spawn: { E: 400.7, N: -363.8, yaw: 0 } },
      hour: {}, players: [], scores: [], vehicles: { upsert: vehicles, remove: [], sources: [] }, serverTime: 0 });
  };
  welcome();
  const physics = { latest: { vehicles: [state] }, spawnVehicle: vi.fn(), removeVehicle: vi.fn(), vehicleImpulse: vi.fn() };
  const parked = { setShared: vi.fn() };
  const shared = new SharedVehicles(client, physics as unknown as PhysicsClient, { vehicles: [] } as unknown as Manifest, parked as unknown as ParkedVehicles);
  shared.drive('truck');
  const update = () => shared.update(performance.now());
  const request = async (action = 'acquire') => {
    update();
    await vi.advanceTimersByTimeAsync(55);
    return Socket.current.sent.filter(m => m.type === 'vehicle' && m.action === action).at(-1)!;
  };
  const reply = async (request: Record<string, unknown>, ok: boolean, v = vehicle, reason?: string) => {
    Socket.current.message({ type: 'vehicle_result', request: request.request, ok, vehicle: v, reason });
    await Promise.resolve();
  };
  return { client, shared, state, vehicle, physics, welcome, update, request, reply };
}

test('a stall longer than the lease, empty snapshots and registry refreshes keep the driven body and never eject', async () => {
  const h = setup();
  await h.reply(await h.request(), true);
  h.physics.spawnVehicle.mockClear();
  await vi.advanceTimersByTimeAsync(65_000);
  h.update();
  expect(h.shared.seatExit(performance.now())).toBeNull();
  Socket.current.message({ type: 'vehicles', upsert: [], remove: [h.vehicle.id] });
  h.update();
  expect(h.physics.removeVehicle).not.toHaveBeenCalled();
  expect(h.shared.seatExit(performance.now())).toBeNull();
  h.welcome([{ ...h.vehicle, occupant: null, revision: 2 }]);
  h.update();
  await vi.advanceTimersByTimeAsync(SEAT_CONFLICT_GRACE_MS + 1);
  expect(h.shared.seatExit(performance.now())).toBeNull();
  expect(h.physics.spawnVehicle).not.toHaveBeenCalled();
  expect(h.physics.removeVehicle).not.toHaveBeenCalled();
});

test('reconnect while driving reclaims the same seat and preserves the current local pose', async () => {
  const h = setup();
  await h.reply(await h.request(), true);
  h.physics.spawnVehicle.mockClear();
  h.state.pos.x += 226;
  Socket.current.close();
  await vi.advanceTimersByTimeAsync(500);
  h.welcome([{ ...h.vehicle, occupant: null, revision: 2 }]);
  const request = await h.request();
  expect(request).toMatchObject({ action: 'acquire', id: h.vehicle.id });
  await h.reply(request, true, { ...h.vehicle, revision: 3 });
  h.update();
  expect(h.shared.hasSeat('truck')).toBe(true);
  expect(h.shared.seatExit(performance.now())).toBeNull();
  expect(h.physics.spawnVehicle).not.toHaveBeenCalled();
  expect(h.physics.removeVehicle).not.toHaveBeenCalled();
});

test('a server restart with a new identity recreates the driven seat at its actual pose', async () => {
  const h = setup();
  await h.reply(await h.request(), true);
  h.physics.spawnVehicle.mockClear();
  h.state.pos.x += 226;
  h.welcome([], 'new-self');
  const request = await h.request();
  expect(request).toMatchObject({ id: 'new-self:truck', source: null, localId: 'truck', pose: { E: h.state.pos.x, vehicle: 'truck' } });
  await h.reply(request, true, { ...h.vehicle, id: 'new-self:truck', creator: 'new-self', owner: 'new-self', occupant: 'new-self' });
  h.update();
  expect(h.shared.record('truck')?.id).toBe('new-self:truck');
  expect(h.physics.removeVehicle).not.toHaveBeenCalled();
  expect(h.physics.spawnVehicle).not.toHaveBeenCalled();
  h.shared.release('truck');
  h.update();
  expect(h.physics.removeVehicle).not.toHaveBeenCalled();
});

test('only an explicit seat_taken denial ejects after grace at the current vehicle driver side, even without a local body', async () => {
  const h = setup();
  const competing = { ...h.vehicle, owner: 'other', occupant: 'other', epoch: 2, revision: 2 };
  Socket.current.message({ type: 'vehicles', upsert: [competing], remove: [] });
  await vi.advanceTimersByTimeAsync(4000);
  expect(h.shared.seatExit(performance.now())).toBeNull(); // a snapshot alone cannot eject
  const request = await h.request();
  await h.reply(request, false, competing, 'seat_taken');
  h.update();
  expect(h.physics.removeVehicle).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(SEAT_CONFLICT_GRACE_MS - 1);
  expect(h.shared.seatExit(performance.now())).toBeNull();
  h.physics.latest.vehicles = [];
  const moved = { ...competing, revision: 3, pose: { ...competing.pose, E: 300, N: -400 } };
  Socket.current.message({ type: 'vehicles', upsert: [moved], remove: [] });
  await vi.advanceTimersByTimeAsync(1);
  expect(h.shared.seatExit(performance.now())).toEqual(driverSide({ ...h.state, pos: { x: 300, y: 4.4, z: 400 } }));
});

test('recreating a borrowed car after a restart keeps its actor identity after the driver exits', async () => {
  const h = setup();
  h.shared.release('truck');
  await vi.advanceTimersByTimeAsync(55);
  const borrowed = { ...h.vehicle, id: 'other:truck', creator: 'other' };
  h.welcome([borrowed]);
  h.state.id = borrowed.id;
  h.shared.drive(borrowed.id);
  await h.reply(await h.request(), true, borrowed);
  h.welcome([], 'new-self');
  const request = await h.request();
  expect(request).toMatchObject({ id: 'new-self:other:truck', localId: 'other:truck' });
  const recreated = { ...borrowed, id: 'new-self:other:truck', localId: borrowed.id,
    creator: 'new-self', owner: 'new-self', occupant: 'new-self' };
  await h.reply(request, true, recreated);
  h.update();
  h.shared.release(borrowed.id);
  h.update();
  expect(h.shared.record(borrowed.id)?.id).toBe(recreated.id);
  expect(h.physics.removeVehicle).not.toHaveBeenCalled();
});

test('recovering ownership during conflict grace cancels the exit; stale replies cannot revoke it', async () => {
  const h = setup();
  const competing = { ...h.vehicle, owner: 'other', occupant: 'other', epoch: 2, revision: 2 };
  await h.reply(await h.request(), false, competing, 'seat_taken');
  Socket.current.message({ type: 'vehicles', upsert: [{ ...h.vehicle, revision: 4 }], remove: [] });
  h.update();
  Socket.current.message({ type: 'vehicles', upsert: [competing], remove: [], patches: [{ id: h.vehicle.id, revision: 3, pose: { E: 900 } }] });
  await vi.advanceTimersByTimeAsync(SEAT_CONFLICT_GRACE_MS + 1);
  expect(h.shared.hasSeat('truck')).toBe(true);
  expect(h.shared.seatExit(performance.now())).toBeNull();
});

test('late reclaim and register replies never release a driven seat; abandoned entry grants are freed', async () => {
  const h = setup();
  const reclaim = await h.request();
  await vi.advanceTimersByTimeAsync(5001);
  await h.reply(reclaim, true);
  const registration = h.client.vehicle({ action: 'register', id: h.vehicle.id });
  await vi.advanceTimersByTimeAsync(55);
  const register = Socket.current.sent.at(-1)!;
  await vi.advanceTimersByTimeAsync(5001);
  expect(await registration).toBeNull();
  await h.reply(register, true);
  await vi.advanceTimersByTimeAsync(55);
  expect(Socket.current.sent.some(m => m.action === 'release')).toBe(false);
  const abandoned = h.client.vehicle({ action: 'acquire', id: 'park:bay' });
  await vi.advanceTimersByTimeAsync(55);
  const entry = Socket.current.sent.at(-1)!;
  await vi.advanceTimersByTimeAsync(5001);
  expect(await abandoned).toBeNull();
  await h.reply(entry, true, { ...h.vehicle, id: 'park:bay' });
  await vi.advanceTimersByTimeAsync(55);
  expect(Socket.current.sent.at(-1)).toMatchObject({ action: 'release', id: 'park:bay' });
});

test('an explicit exit during disconnection releases the reservation after reconnect', async () => {
  const h = setup();
  await h.reply(await h.request(), true);
  Socket.current.close();
  h.shared.release('truck');
  await vi.advanceTimersByTimeAsync(500);
  h.welcome();
  await vi.advanceTimersByTimeAsync(55);
  expect(Socket.current.sent.at(-1)).toMatchObject({ action: 'release', id: h.vehicle.id });
});
