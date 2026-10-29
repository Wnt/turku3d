import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { RemotePlayers } from '../../src/net/remotes';
import { MultiplayerClient } from '../../src/net/client';
import type { PhysicsClient } from '../../src/physics/client';
import type { SharedVehicles } from '../../src/net/sharedVehicles';

// Headless scene graph: no renderer, screenshots, browser or drive route.
test('a released remote car keeps its paint/pose/proxy after its driver leaves the roster', () => {
  vi.stubGlobal('document', { createElement: () => ({ getContext: () => ({ fillRect() {}, fillText() {}, measureText: (text: string) => ({ width: text.length * 18 }) }) }) });
  const root = new THREE.Group(), car = new THREE.Group(), scooter = new THREE.Group();
  const lean = new THREE.Group(); lean.name = 'scooter-lean'; scooter.add(lean);
  const red = new THREE.Mesh(new THREE.BoxGeometry(4, 1, 2), new THREE.MeshStandardMaterial({ color: 0xff0000 }));
  const physics = { loaded: new Set(['c_2_4']), setRemotes: vi.fn() };
  const sources = { player: { root: new THREE.Group() }, car: { root: car }, scooter: { root: scooter }, truck: { root: car },
    parked: { remoteModel: () => red.clone(), requestRemoteModel: vi.fn() } };
  const effects = { reserve: () => 1, release: vi.fn() };
  const remotes = new RemotePlayers(root, physics as unknown as PhysicsClient,
    sources as unknown as ConstructorParameters<typeof RemotePlayers>[2], effects as unknown as ConstructorParameters<typeof RemotePlayers>[3]);
  const client = new MultiplayerClient('ws://localhost:5403', null);
  client.state.connected = true; client.state.you = { id: 'self', nick: 'AA', spawn: { E: 170, N: -252, yaw: 0 } };
  const pose = { t: 1, E: 170, N: -252, H: 4, yaw: 0, vE: 0, vN: 0, vH: 0, mode: 'car' as const,
    vehicle: 'sedan/red', carrying: false, rot: { x: .6, y: 0, z: 0, w: .8 } };
  client.state.vehicles.set('park:bay', { id: 'park:bay', source: 'bay', creator: 'other', localId: 'bay',
    owner: 'other', occupant: null, epoch: 1, revision: 2, half: { x: 2, y: .3, z: 1 }, pose });
  const shared = { localId: () => 'park:bay' } as unknown as SharedVehicles;
  try {
    remotes.update(client, 100, { x: 170, y: 4, z: 252 }, 0, shared);
    expect(remotes.visualBounds()).toEqual([expect.objectContaining({ id: 'park:bay', vehicle: 'sedan/red', paint: 'ff0000', x: 170 })]);
    expect(physics.setRemotes).toHaveBeenLastCalledWith([expect.objectContaining({ id: 'park:bay', rot: pose.rot, mode: 'car' })]);
    client.state.players = [];
    remotes.update(client, 300, { x: 170, y: 4, z: 252 }, 0, shared);
    expect(remotes.visualBounds()).toHaveLength(1);
    client.state.vehicles.clear(); remotes.update(client, 400, { x: 170, y: 4, z: 252 }, 0, shared);
    expect(remotes.visualBounds()).toHaveLength(0);
    expect(physics.setRemotes).toHaveBeenLastCalledWith([]);
  } finally { remotes.dispose(); vi.unstubAllGlobals(); }
});
