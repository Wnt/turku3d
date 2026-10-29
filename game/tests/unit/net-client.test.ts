import { expect, test } from 'vitest';
import { multiplayerUrl, scoresUrl } from '../../src/net/config';
import { shouldSendPose, type NetPose } from '../../src/net/client';

const pose: NetPose = { t: 0, E: 168, N: -246, H: 3, yaw: 0, vE: 0, vN: 0, vH: 0,
  mode: 'foot', vehicle: null, carrying: false };

test('endpoint selection uses localhost for local pages and permits a WebSocket override', () => {
  expect(multiplayerUrl({ hostname: 'localhost', search: '' })).toBe('ws://127.0.0.1:7700/');
  expect(multiplayerUrl({ hostname: 'wnt.github.io', search: '' })).toBe('wss://turku.lab.madekivi.fi/');
  expect(multiplayerUrl({ hostname: 'localhost', search: '?mp=ws%3A%2F%2Flocalhost%3A5235' })).toBe('ws://localhost:5235/');
  expect(() => multiplayerUrl({ hostname: 'localhost', search: '?mp=https%3A%2F%2Fexample.com' })).toThrow();
  expect(scoresUrl('wss://turku.lab.madekivi.fi/')).toBe('https://turku.lab.madekivi.fi');
});

test('pose rate keeps within 20 messages per second and sends a stationary heartbeat', () => {
  expect(shouldSendPose(0, null, -Infinity, pose)).toBe(true);
  expect(shouldSendPose(50, pose, 0, { ...pose, E: 169 })).toBe(false);
  expect(shouldSendPose(55, pose, 0, { ...pose, E: 169 })).toBe(true);
  expect(shouldSendPose(999, pose, 0, pose)).toBe(false);
  expect(shouldSendPose(1000, pose, 0, pose)).toBe(true);
  expect(shouldSendPose(55, pose, 0, { ...pose, mode: 'car', vehicle: 'fixture/red' })).toBe(true);
  expect(shouldSendPose(55, { ...pose, mode: 'car', vehicle: 'fixture/blue' }, 0,
    { ...pose, mode: 'car', vehicle: 'fixture/red' })).toBe(true);
});


test('pitch, roll and wheel changes send a pose even without movement or heading change', () => {
  const car = { ...pose, mode: 'car' as const, vehicle: 'player', rot: { x: 0, y: 0, z: 0, w: 1 } };
  expect(shouldSendPose(55, car, 0, { ...car, rot: { x: 0.1, y: 0, z: 0, w: Math.sqrt(0.99) } })).toBe(true);
  expect(shouldSendPose(55, car, 0, { ...car, wheels: [{ pos: { x: 1, y: 0, z: 1 }, steer: 0.2, rotation: 1, suspension: 0.2, contact: true }] })).toBe(true);
});

// Exercise the real client send queue: simultaneous pole/fine, deliveries and
// seat requests must consume the same transport budget as moving poses.
test('all client messages share the pose rate budget; mismatch stops reconnect and requests reload', async () => {
  const { vi } = await import('vitest');
  const { MultiplayerClient } = await import('../../src/net/client');
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
  class Socket extends EventTarget {
    static OPEN = 1;
    static current: Socket;
    readyState = 1;
    sent: { at: number; message: Record<string, unknown> }[] = [];
    constructor() { super(); Socket.current = this; }
    send(data: string) { this.sent.push({ at: performance.now(), message: JSON.parse(data) }); }
    close() { this.readyState = 3; this.dispatchEvent(new Event('close')); }
    message(data: object) { this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(data) })); }
  }
  vi.stubGlobal('WebSocket', Socket);
  const client = new MultiplayerClient('ws://localhost:5403', null);
  try {
    client.connect();
    const socket = Socket.current;
    socket.dispatchEvent(new Event('open'));
    socket.message({ v: 2, type: 'welcome', session: 'a'.repeat(64), you: { id: 'self', nick: 'AA', spawn: { E: 0, N: 0, yaw: 0 } },
      hour: {}, players: [], scores: [], vehicles: { upsert: [], remove: [], sources: [] }, serverTime: Date.now() });
    client.breakPole('test');
    client.sendDelivery({ orderId: 'test', addressId: 'shop', payout: 6, tip: 0, routeMeters: 100 });
    const request = client.vehicle({ action: 'acquire', id: 'park:bay' });
    for (let i = 0; i < 100; i++) { client.sendPose({ ...pose, E: i }); await vi.advanceTimersByTimeAsync(10); }
    for (let i = 1; i < socket.sent.length; i++) expect(socket.sent[i].at - socket.sent[i - 1].at).toBeGreaterThanOrEqual(55);
    expect(socket.sent.length).toBeLessThanOrEqual(20);
    expect(socket.sent.map(s => s.message.type)).toEqual(expect.arrayContaining(['world', 'fine', 'delivery', 'vehicle', 'pose']));
    socket.message({ v: 1, type: 'error', code: 'protocol', reload: true });
    expect(client.state.error).toContain('Reload'); expect(client.state.connected).toBe(false);
    expect(await request).toBeNull();
    await vi.advanceTimersByTimeAsync(16000); expect(Socket.current).toBe(socket);
  } finally { client.disconnect(); vi.useRealTimers(); vi.unstubAllGlobals(); }
});
