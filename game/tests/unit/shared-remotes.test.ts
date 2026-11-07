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

test('the driven shared vehicle this player owns is drawn at its render pose, not the newer raw physics state', () => {
  vi.stubGlobal('document', { createElement: () => ({ getContext: () => ({ fillRect() {}, fillText() {}, measureText: (text: string) => ({ width: text.length * 18 }) }) }) });
  const root = new THREE.Group(), car = new THREE.Group(), scooter = new THREE.Group();
  const lean = new THREE.Group(); lean.name = 'scooter-lean'; scooter.add(lean);
  const red = new THREE.Mesh(new THREE.BoxGeometry(4, 1, 2), new THREE.MeshStandardMaterial({ color: 0xff0000 }));
  const state = (x: number) => ({ id: 'park:bay', pos: { x, y: 4.6, z: 252 }, rot: { x: 0, y: 0, z: 0, w: 1 }, linvel: { x: 10, y: 0, z: 0 },
    speed: 10, forwardSpeed: 10, curbStrike: null, wheels: [] });
  const physics = { loaded: new Set(['c_2_4']), setRemotes: vi.fn(), latest: { vehicles: [state(180)] } };
  const sources = { player: { root: new THREE.Group() }, car: { root: car }, scooter: { root: scooter }, truck: { root: car },
    parked: { remoteModel: () => red.clone(), requestRemoteModel: vi.fn() } };
  const effects = { reserve: () => 1, release: vi.fn() };
  const remotes = new RemotePlayers(root, physics as unknown as PhysicsClient,
    sources as unknown as ConstructorParameters<typeof RemotePlayers>[2], effects as unknown as ConstructorParameters<typeof RemotePlayers>[3]);
  const client = new MultiplayerClient('ws://localhost:5403', null);
  client.state.connected = true; client.state.you = { id: 'self', nick: 'AA', spawn: { E: 170, N: -252, yaw: 0 } };
  const pose = { t: 1, E: 170, N: -252, H: 4, yaw: 0, vE: 0, vN: 0, vH: 0, mode: 'car' as const, vehicle: 'sedan/red', carrying: false };
  client.state.vehicles.set('park:bay', { id: 'park:bay', source: 'bay', creator: 'self', localId: 'park:bay',
    owner: 'self', occupant: 'self', epoch: 1, revision: 2, half: { x: 2, y: .3, z: 1 }, pose });
  const shared = { localId: () => 'park:bay' } as unknown as SharedVehicles;
  try {
    remotes.update(client, 100, { x: 170, y: 4, z: 252 }, 0, shared, id => id === 'park:bay' ? state(175) : null);
    expect(remotes.visualBounds()).toEqual([expect.objectContaining({ id: 'park:bay', x: 175 })]);
    remotes.update(client, 116, { x: 170, y: 4, z: 252 }, 0, shared);
    expect(remotes.visualBounds()).toEqual([expect.objectContaining({ id: 'park:bay', x: 180 })]);
  } finally { remotes.dispose(); vi.unstubAllGlobals(); }
});

test('remote scooters and trucks join the scene only while ridden: 40 parked shared cars add no hidden truck rigs', () => {
  vi.stubGlobal('document', { createElement: () => ({ getContext: () => ({ fillRect() {}, fillText() {}, measureText: (text: string) => ({ width: text.length * 18 }) }) }) });
  const root = new THREE.Group(), car = new THREE.Group(), scooter = new THREE.Group();
  const lean = new THREE.Group(); lean.name = 'scooter-lean'; scooter.add(lean);
  // A truck rig with a hundred bones, as actors/truck.ts builds it.
  const truck = new THREE.Group(); truck.name = 'truck';
  for (let i = 0, parent: THREE.Object3D = truck; i < 100; i++) { const bone = new THREE.Bone(); parent.add(bone); parent = bone; }
  const red = new THREE.Mesh(new THREE.BoxGeometry(4, 1, 2), new THREE.MeshStandardMaterial({ color: 0xff0000 }));
  const physics = { loaded: new Set(['c_2_4']), setRemotes: vi.fn() };
  const sources = { player: { root: new THREE.Group() }, car: { root: car }, scooter: { root: scooter }, truck: { root: truck },
    parked: { remoteModel: () => red.clone(), requestRemoteModel: vi.fn() } };
  const remotes = new RemotePlayers(root, physics as unknown as PhysicsClient,
    sources as unknown as ConstructorParameters<typeof RemotePlayers>[2], { reserve: () => 1, release: vi.fn() } as unknown as ConstructorParameters<typeof RemotePlayers>[3]);
  const client = new MultiplayerClient('ws://localhost:5403', null);
  client.state.connected = true; client.state.you = { id: 'self', nick: 'AA', spawn: { E: 170, N: -252, yaw: 0 } };
  const pose = (mode: 'car' | 'truck' | 'foot', E: number) => ({ t: E, E, N: -252, H: 4, yaw: 0, vE: 0, vN: 0, vH: 0, mode,
    vehicle: mode === 'car' ? 'sedan/red' : mode === 'truck' ? 'truck' : null, carrying: false, rot: { x: 0, y: 0, z: 0, w: 1 } });
  for (let i = 0; i < 40; i++) client.state.vehicles.set(`park:${i}`, { id: `park:${i}`, source: `${i}`, creator: 'other', localId: `${i}`,
    owner: 'other', occupant: null, epoch: 1, revision: 2, half: { x: 2, y: .3, z: 1 }, pose: pose('car', 170 + i * .4) } as never);
  const shared = { localId: (v: { id: string }) => v.id } as unknown as SharedVehicles;
  const objects = () => { let n = 0; root.traverse(() => { n++; }); return n; };
  try {
    remotes.update(client, 100, { x: 170, y: 4, z: 252 }, 0, shared);
    expect(remotes.visualBounds()).toHaveLength(40);
    // Each parked record: its distant figure, car model, label and pizza (~9 objects); no scooter or 100-bone truck
    // clone (before: ~112 objects per record, 4,480 here).
    expect(objects()).toBeLessThan(40 * 12);
    let found = 0; root.traverse(o => { if (o.name === 'truck') found++; });
    expect(found).toBe(0);
    // A truck in the room is cloned once, drawn, and leaves the scene when its driver walks away.
    client.state.players = [{ id: 'p1', nick: 'BB', spawn: { E: 170, N: -252, yaw: 0 }, pose: pose('truck', 180) } as never];
    remotes.update(client, 200, { x: 170, y: 4, z: 252 }, 0, shared);
    expect(remotes.visualBounds().find(b => b.id === 'p1')?.mode).toBe('truck');
    found = 0; root.traverse(o => { if (o.name === 'truck') found++; });
    expect(found).toBe(1);
    client.state.players = [{ id: 'p1', nick: 'BB', spawn: { E: 170, N: -252, yaw: 0 }, pose: pose('foot', 181) } as never];
    remotes.update(client, 1000, { x: 170, y: 4, z: 252 }, 0, shared);
    found = 0; root.traverse(o => { if (o.name === 'truck') found++; });
    expect(found).toBe(0);
  } finally { remotes.dispose(); vi.unstubAllGlobals(); }
});
