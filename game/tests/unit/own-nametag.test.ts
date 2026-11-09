import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { RemotePlayers, nameTagFade, showsNameTag } from '../../src/net/remotes';
import { MultiplayerClient } from '../../src/net/client';
import type { PhysicsClient } from '../../src/physics/client';
import type { SharedVehicles } from '../../src/net/sharedVehicles';

test('the local player id never gets a name tag, on foot or in a vehicle', () => {
  expect(showsNameTag({ id: 'self', nick: 'Me' }, 'self')).toBe(false);
  expect(showsNameTag({ id: 'park:bay', nick: 'Me', occupant: 'self' }, 'self')).toBe(false);
  expect(showsNameTag({ id: 'other', nick: 'You' }, 'self')).toBe(true);
  expect(showsNameTag({ id: 'park:bay', nick: 'You', occupant: 'other' }, 'self')).toBe(true);
  expect(showsNameTag({ id: 'park:bay', nick: '', occupant: null }, 'self')).toBe(false);
});

test('remote tags fade near the camera and off the screen centre but stay readable far away', () => {
  const side = Math.cos(Math.PI / 6);
  expect(nameTagFade(2, side).opacity).toBe(0);
  expect(nameTagFade(20, side).opacity).toBe(1);
  expect(nameTagFade(15, 1).opacity).toBe(0);
  expect(nameTagFade(120, 1)).toEqual({ opacity: 1, scale: 4 });
});

test('a shared vehicle driven by the local player renders without its tag and off the minimap', () => {
  vi.stubGlobal('document', { createElement: () => ({ getContext: () => ({ fillRect() {}, fillText() {}, measureText: (text: string) => ({ width: text.length * 18 }) }) }) });
  const root = new THREE.Group(), car = new THREE.Group(), scooter = new THREE.Group();
  const lean = new THREE.Group(); lean.name = 'scooter-lean'; scooter.add(lean);
  const physics = { loaded: new Set(['c_2_4']), setRemotes: vi.fn(), remoteCarRender: { sample: () => [] }, latest: { vehicles: [] } };
  const sources = { player: { root: new THREE.Group() }, car: { root: car }, scooter: { root: scooter }, truck: { root: car },
    parked: { remoteModel: () => new THREE.Group(), requestRemoteModel: vi.fn() } };
  const effects = { reserve: () => 1, release: vi.fn() };
  const remotes = new RemotePlayers(root, physics as unknown as PhysicsClient,
    sources as unknown as ConstructorParameters<typeof RemotePlayers>[2], effects as unknown as ConstructorParameters<typeof RemotePlayers>[3]);
  const client = new MultiplayerClient('ws://localhost:5403', null);
  client.state.connected = true; client.state.you = { id: 'self', nick: 'FlyingWillow', spawn: { E: 170, N: -252, yaw: 0 } };
  const pose = { t: 1, E: 170, N: -252, H: 4, yaw: 0, vE: 0, vN: 0, vH: 0, mode: 'car' as const,
    vehicle: 'player', carrying: false, vehicleId: 'park:bay' };
  client.state.players = [{ id: 'self', nick: 'FlyingWillow', spawn: client.state.you.spawn, pose }];
  client.state.vehicles.set('park:bay', { id: 'park:bay', source: 'bay', creator: 'self', localId: 'park:bay',
    owner: 'self', occupant: 'self', epoch: 1, revision: 2, half: { x: 2, y: .3, z: 1 }, pose });
  const shared = { localId: () => 'park:bay' } as unknown as SharedVehicles;
  const tags = () => root.children.filter(c => c instanceof THREE.Sprite && c.visible);
  try {
    remotes.update(client, 100, { x: 170, y: 4, z: 252 }, 0, shared);
    expect(remotes.visualBounds()).toHaveLength(1);
    expect(tags()).toHaveLength(0);
    expect(remotes.markers()).toHaveLength(0);
    client.state.players.push({ id: 'other', nick: 'RapidLeaf', spawn: client.state.you.spawn, pose: { ...pose, vehicleId: undefined, E: 172, mode: 'foot' } });
    remotes.update(client, 200, { x: 170, y: 4, z: 252 }, 0, shared);
    expect(tags()).toHaveLength(1);
  } finally { remotes.dispose(); vi.unstubAllGlobals(); }
});
