import { afterEach, describe, expect, it, vi } from 'vitest';
import { SaveGameController } from '../../src/gameplay/saveGame';
import { captureVehicle, restoreVehicleSpot, roadSpots, vehicleGround } from '../../src/gameplay/vehicleSave';
import { startPoints } from '../../src/gameplay/startPoints';
import { parseRoadGraph } from '../../src/gameplay/roadGraph';
import { SaveStore, SAVES_KEY, type SavedVehicle } from '../../src/core/save';
import { draftManifest } from '../../src/world/manifest';
import { enToEngine } from '../../src/core/origin';
import { MAT } from '../../src/shared/col/materials';
import type { PhysicsClient } from '../../src/physics/client';
import type { VehicleState } from '../../src/physics/protocol';

const saved: SavedVehicle = { id: 'rental-17', kind: 'car', seated: true, E: 23459510, N: 6704480, H: 2.75, yaw: 0.8 };
const graph = parseRoadGraph({ format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', bbox: [0, 0, 50, 50],
  nodes: [0, 0, 50, 0, 0, 50], edges: [[0, 1, 3, 0], [0, 2, 1, 0]] });
const state = (id = saved.id, kind: 'car' | 'truck' | 'scooter' = 'car'): VehicleState => ({ id, kind,
  pos: { x: 10, y: 2.75, z: 20 }, rot: { x: 0, y: Math.sin(0.4), z: 0, w: Math.cos(0.4) }, linvel: { x: 0, y: 0, z: 0 },
  speed: 0, forwardSpeed: 0, wheels: [], curbStrike: null });
const manifest = () => draftManifest('http://localhost/');
const hit = (material: number = MAT.road, y = 2) => ({ toi: 2, point: { x: 10, y, z: 20 }, normal: { x: 0, y: 1, z: 0 }, material, tile: 'c_0_0' });
afterEach(() => vi.unstubAllGlobals());

describe('last-used vehicle save', () => {
  it.each(['player', 'truck', 'scooter', 'rental-17', 'mission-2'])('round trips identity, kind, seated state and pose: %s', id => {
    const kind = id === 'truck' || id === 'scooter' ? id : 'car';
    const last = captureVehicle(state(id, kind), true, saved.yaw, manifest());
    expect(last).toEqual({ ...saved, id, kind });
    const store = { value: '', getItem(k: string) { return k === SAVES_KEY ? this.value || null : null; }, setItem(_k: string, v: string) { this.value = v; }, removeItem() {} };
    const saves = new SaveStore(store, () => ({ deliveries: 0, money: 0, devMoney: 0, lastAddress: null }));
    const game = { version: 1 as const, savedAt: 1, label: '', mission: '{}', player: null, vehicle: null, clock: null,
      stats: { deliveries: 0, money: 0 }, lastVehicle: last };
    saves.write('auto', game);
    expect(new SaveStore(store, () => { throw Error('no migration'); }).get('auto')).toEqual(game);
    expect(startPoints({ x: 0, y: 0, z: 0 }, { x: 1, y: 0, z: 1 }, [game], null)).toContainEqual(enToEngine(saved.E, saved.N, saved.H));
    store.setItem(SAVES_KEY, JSON.stringify({ version: 1, slots: { auto: { ...game, lastVehicle: { ...last, kind: 'boat' } } } }));
    expect(new SaveStore(store, () => { throw Error('no migration'); }).any).toBe(false);
  });

  it('keeps a borrowed model and dimensions, and never assigns them to the next vehicle', () => {
    const m = manifest();
    const appearance = { object_id: saved.id, model: 'van', color: 'blue', x: 10, y: 2, z: 20, yaw: 0,
      width: 2, height: 2, length: 5, clear_front_m: 2, clear_rear_m: 2 };
    const modelAsset = { url: 'van-blue.glb', sha256: 'abc', bytes: 100 };
    m.vehicles = [appearance];
    m.vehicle_models = { 'van/blue': modelAsset };
    const last = captureVehicle(state(), false, 0.8, m)!;
    expect(last).toMatchObject({ appearance, modelAsset, seated: false });
    expect(captureVehicle(state(), false, 0.8, manifest(), last)).toEqual(last);
    expect(captureVehicle(state('player'), true, 0.8, manifest(), last)?.appearance).toBeUndefined();
  });

  it('keeps old v1 saves readable without lastVehicle', () => {
    const raw = JSON.stringify({ version: 1, slots: { auto: { version: 1, savedAt: 1, label: '', mission: '{}', player: null,
      vehicle: null, clock: null, stats: { deliveries: 0, money: 0 } } } });
    expect(new SaveStore({ getItem: (k: string) => k === SAVES_KEY ? raw : null, setItem() {}, removeItem() {} }, () => { throw Error(); }).get('auto')?.lastVehicle).toBeUndefined();
  });

  it('restores exact valid pose; otherwise chooses nearest supported drivable road beside player', async () => {
    const ground = vi.fn(async (): Promise<number | null> => 2);
    expect(await restoreVehicleSpot(saved, { x: 4, y: 2, z: 5 }, graph, ground, 0.75))
      .toEqual({ x: 10, y: 2.75, z: 20, yaw: saved.yaw });
    ground.mockResolvedValueOnce(null).mockResolvedValueOnce(null);
    const spot = await restoreVehicleSpot(saved, { x: 4, y: 2, z: 5 }, graph, ground, 0.75);
    expect(spot.z).toBe(0); // foot-only path at x=0 is nearer but cannot carry cars
    expect(spot.y).toBe(2.75);
    expect(ground.mock.calls.length).toBeGreaterThan(2);
    expect(roadSpots(graph, { x: 4, y: 2, z: 5 })[0]).toMatchObject({ x: 4, z: 0 });
    await expect(restoreVehicleSpot(saved, { x: 4, y: 2, z: 5 }, graph, async () => null, 0.75)).rejects.toThrow('No collision-backed');
  });

  it.each(['missing', 'building', 'roof', 'underwater', 'wall', 'unsupported'])('rejects unsafe placement: %s', async reason => {
    const physics = {
      raycast: vi.fn(async (_p, dir, _d, filter) => {
        if (filter === 'water') return reason === 'underwater' ? hit(MAT.water, 3) : null;
        if (dir.y === 1) return reason === 'building' ? hit(MAT.roof, 10) : null;
        if (reason === 'missing') return null;
        if (reason === 'roof') return hit(MAT.roof);
        if (reason === 'unsupported' && _p.x !== 10) return null;
        return hit();
      }),
      cameraCast: vi.fn(async (_p, _d, distance) => reason === 'wall' ? 0 : distance),
    } as unknown as PhysicsClient;
    expect(await vehicleGround(physics, { x: 10, y: 2.75, z: 20, yaw: 0 }, saved, 0.75, false)).toBeNull();
  });

  it('accepts supported ground under a high bridge and preserves the road below it', async () => {
    const physics = { raycast: vi.fn(async (_p, dir, _d, filter) => filter === 'water' ? null
      : dir.y === 1 ? { ...hit(MAT.bridge_deck, 10), toi: 8 } : hit()),
    cameraCast: vi.fn(async (_p, _d, distance) => distance) } as unknown as PhysicsClient;
    expect(await vehicleGround(physics, { x: 10, y: 2.75, z: 20, yaw: 0 }, saved, 0.75, false)).toBe(2);
  });

  it.each([true, false])('controller captures, reloads and restores a non-owned truck (seated=%s)', async seated => {
    vi.stubGlobal('window', new EventTarget());
    vi.stubGlobal('document', Object.assign(new EventTarget(), { visibilityState: 'visible' }));
    const vehicle = state('truck', 'truck');
    const physics = {
      latest: { vehicles: [vehicle], character: { pos: { x: 10, y: 2, z: 20 } } }, loaded: new Map([['c_0_0', {}]]),
      resetVehicle: vi.fn(), spawnVehicle: vi.fn((pos, _yaw, spec) => { vehicle.id = spec.id; vehicle.pos = pos; }),
      setCharacterEnabled: vi.fn((_on, pos) => { if (pos) physics.latest.character.pos = pos; }), step: vi.fn(),
      driveVehicle: vi.fn(), raycast: vi.fn(async (_p, dir, _d, filter) => filter === 'water' || dir.y === 1 ? null : hit()),
      cameraCast: vi.fn(async (_p, _d, distance) => distance),
    };
    let mode: 'car' | 'foot' = 'car';
    const o = {
      physics, streamer: { update() {} }, manifest: manifest(), router: { graph }, missions: { vehicle: 'none',
        lastAddress: null, serialize: () => '{}', restore: vi.fn(), begin: vi.fn(), deliveries: 0, money: 0, devMoney: 0 },
      recovery: { restart: vi.fn() }, saves: new SaveStore(null, () => { throw Error(); }),
      startScreen: { active: false, close() {}, show() {} }, pauseMenu: { close() {}, show() {} },
      clock: () => new Date(), spawn: { x: 10, y: 2, z: 20, yaw: 0 }, car: { x: 15, y: 2, z: 20, yaw: 0 },
      view: () => ({ mode, drivenId: mode === 'car' ? 'truck' : null, feet: { x: 10, y: 2, z: 20 }, cameraYaw: 1 }),
      leaveCar: () => { mode = 'foot'; }, placed: vi.fn(), enterVehicle: vi.fn(), setPaused: vi.fn(),
      showIntro: vi.fn(), fail: vi.fn(),
    };
    const controller = new SaveGameController(o as unknown as ConstructorParameters<typeof SaveGameController>[0]);
    controller.usedVehicle('truck');
    if (!seated) mode = 'foot';
    window.dispatchEvent(new Event('pagehide'));
    const game = o.saves.get('auto')!;
    expect(game.lastVehicle).toMatchObject({ id: 'truck', kind: 'truck', seated, yaw: expect.closeTo(0.8) });
    expect(game.vehicle).toBeNull();
    controller.dispose();
    // A fresh controller has no last-used memory: all restore decisions must come from the serialized save.
    const reload = new SaveGameController(o as unknown as ConstructorParameters<typeof SaveGameController>[0]);
    await reload.prepare(game);
    reload.hooks.play(game);
    await vi.waitFor(() => expect(o.setPaused).toHaveBeenLastCalledWith(false));
    expect(o.fail).not.toHaveBeenCalled();
    expect(physics.spawnVehicle).toHaveBeenCalledWith(expect.objectContaining({ x: 10, z: 20 }), expect.closeTo(0.8), expect.objectContaining({ id: 'truck', kind: 'truck' }));
    expect(o.enterVehicle).toHaveBeenCalledTimes(seated ? 1 : 0);
    expect(reload.capture().lastVehicle?.id).toBe('truck');
    reload.dispose();
  });
});
