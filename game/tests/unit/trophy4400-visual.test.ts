import { describe, expect, it, beforeAll } from 'vitest';
import { readFileSync, statSync } from 'node:fs';
import * as T from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { clone as cloneSkinned } from 'three/addons/utils/SkeletonUtils.js';
import { build, instance, type TrophyAsset } from '../../src/actors/trophy4400/asset';
import { GROUND_Y, applyTrophyRig, staticWheels, trophyRigSolves, useTrophyAsset } from '../../src/actors/trophy4400/rig';
import { TRUCK, TRUCK_STATIC_LENGTH } from '../../src/physics/constants';
import { vehicleLamps } from '../../src/vehicle/lamps';
import { SeatedVehicles } from '../../src/actors/seatedVehicles';

const dir = new URL('../../public/assets/vehicles/trophy4400/', import.meta.url);
let asset: TrophyAsset;

beforeAll(async () => {
  (globalThis as { self?: unknown }).self ??= globalThis;
  const bytes = readFileSync(new URL('trophy4400.glb', dir));
  // Node has no KTX2 transcoder; the geometry and node contract are what these checks read.
  const ktx2 = { load: (_: string, onLoad: (t: T.Texture) => void) => onLoad(new T.Texture()), detectSupport() { return this; } };
  const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder).setKTX2Loader(ktx2 as never);
  const gltf = await loader.parseAsync(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength), '');
  asset = build(gltf.scene.getObjectByName('trophy4400')!, JSON.parse(readFileSync(new URL('hardpoints.json', dir), 'utf8')));
  useTrophyAsset(asset);
});

const truckRoot = () => {
  const root = new T.Group(), model = instance(asset);
  model.position.y = GROUND_Y; root.add(model);
  for (let i = 0; i < 4; i++) { const m = new T.Object3D(); m.name = `vehicle-wheel-${i}`; root.add(m); }
  return root;
};
const bone = (root: T.Object3D, name: string) => root.getObjectByName(name)!;

describe('trophy4400 model (SPEC M1/M2)', () => {
  it('fits the budgets: triangles and draws per LOD, file size', () => {
    console.log('trophy4400 budget', asset.triangles, asset.draws);
    expect(asset.triangles[0]).toBeLessThanOrEqual(160_000);
    expect(asset.triangles[1]).toBeLessThanOrEqual(30_000);
    expect(asset.triangles[2]).toBeLessThanOrEqual(4_000);
    // One rigid-skinned draw per material per LOD.
    expect(asset.draws[0]).toBeLessThanOrEqual(40);
    expect(asset.draws[2]).toBeLessThanOrEqual(30);
    expect(statSync(new URL('trophy4400.glb', dir)).size).toBeLessThan(12e6);
  });

  it('draws the body at the truck size in every LOD', () => {
    const root = truckRoot();
    applyTrophyRig(root, staticWheels());
    root.updateMatrixWorld(true);
    const levels = (root.getObjectByName('trophy4400-lod') as T.LOD).levels.map(l => l.object);
    // Every level draws the orange body; L0 also the cage, the tyres and the rims.
    for (const level of levels) expect(level.children.map(m => (m as T.Mesh).material as T.Material).map(m => m.name)).toContain('paint-orange');
    const l0 = new Set(levels[0].children.map(m => ((m as T.Mesh).material as T.Material).name));
    for (const name of ['paint-orange', 'cage-powder', 'rubber', 'wheel-gunmetal']) expect(l0.has(name)).toBe(true);
    // Skinned L0 bounds in the chassis frame (+X forward, +Z right), as the GPU draws them at the static ride.
    const box = new T.Box3(), v = new T.Vector3();
    for (const mesh of levels[0].children as T.SkinnedMesh[]) {
      mesh.skeleton.update();
      const position = mesh.geometry.getAttribute('position');
      for (let i = 0; i < position.count; i++) box.expandByPoint(mesh.getVertexPosition(i, v).applyMatrix4(mesh.matrixWorld));
    }
    const size = box.getSize(new T.Vector3());
    // SPEC C1: overall length 4.55 (spare and rear tube included), 2.55 wide at the tyres, light bar top ~1.97.
    expect(size.x).toBeGreaterThan(4.3); expect(size.x).toBeLessThan(5.0);
    expect(size.z).toBeGreaterThan(2.4); expect(size.z).toBeLessThan(2.7);
    expect(Math.abs(box.min.y - GROUND_Y)).toBeLessThan(0.05);
    expect(box.max.y - GROUND_Y).toBeGreaterThan(1.8);
    expect(box.min.x).toBeLessThan(-2); expect(box.max.x).toBeGreaterThan(1.8);
    expect(Math.abs(box.min.z + box.max.z)).toBeLessThan(0.1);
  });

  it('prepares the seated vehicles as they load, before either is drawn', async () => {
    const prepared: string[] = [];
    const seated = new SeatedVehicles(new T.Group(), { spawnVehicle: () => {} }, async () => 0, async model => { prepared.push(model.name); });
    // The network loads are covered above; here only the hand-over to the actor preparation lane.
    seated.scooter.load = async () => { seated.scooter.loaded = true; };
    seated.truck.load = async () => { seated.truck.loaded = true; };
    await seated.load('/');
    expect(prepared.sort()).toEqual(['scooter', 'truck']);
    expect(seated.truck.root.visible).toBe(false);
  });

  it('has the wheels on the physics layout and the static ride on the ground', () => {
    const root = truckRoot();
    applyTrophyRig(root, staticWheels());
    root.updateMatrixWorld(true);
    TRUCK.wheels.forEach((at, i) => {
      const hub = bone(root, `upright-${['fl', 'fr', 'rl', 'rr'][i]}`).getWorldPosition(new T.Vector3());
      expect(Math.abs(hub.x - at.x)).toBeLessThan(0.01);
      expect(Math.abs(hub.z - at.z)).toBeLessThan(0.01);
      expect(hub.y).toBeCloseTo(at.y - TRUCK_STATIC_LENGTH, 3);
    });
    // Lamps: lenses use the materials vehicle/lamps.ts drives.
    const names = new Set<string>(); root.traverse(o => { const m = (o as T.Mesh).material as T.Material | undefined; if (m) names.add(m.name); });
    expect(names.has('led-optics')).toBe(true); expect(names.has('tail-lens')).toBe(true);
    vehicleLamps(root, 'truck', 2);
  });

  it('articulates a remote clone on its own bones from the synced wheels', () => {
    const local = truckRoot();
    applyTrophyRig(local, staticWheels());
    const remote = cloneSkinned(local);
    const wheels = staticWheels();
    wheels[0].pos.y += 0.30; wheels[1].pos.y -= 0.25; wheels[2].steer = -0.5; wheels[3].steer = -0.3;
    applyTrophyRig(remote, wheels);
    expect(trophyRigSolves(remote)).toBe(1);
    remote.updateMatrixWorld(true); local.updateMatrixWorld(true);
    const fl = bone(remote, 'upright-fl').getWorldPosition(new T.Vector3()), flLocal = bone(local, 'upright-fl').getWorldPosition(new T.Vector3());
    expect(fl.y - flLocal.y).toBeCloseTo(0.30, 3);
    expect(bone(remote, 'upright-fr').getWorldPosition(new T.Vector3()).y - flLocal.y).toBeCloseTo(-0.25, 3);
    // The arms swing about their inboard axes, the springs compress, the rear uprights carry the rear steer.
    expect(bone(remote, 'lca-fl').quaternion.angleTo(bone(local, 'lca-fl').quaternion)).toBeGreaterThan(0.2);
    expect(bone(remote, 'coil-fl-spring').scale.y).toBeLessThan(0.95);
    expect(bone(remote, 'coil-fr-spring').scale.y).toBeGreaterThan(1.05);
    const heading = new T.Vector3(1, 0, 0).applyQuaternion(bone(remote, 'upright-rl').quaternion);
    expect(Math.atan2(-heading.z, heading.x)).toBeCloseTo(-0.5, 1);
    // The skinned meshes of the clone are bound to the clone's bones, not the source's.
    let foreign = 0;
    remote.traverse(o => { const s = o as T.SkinnedMesh; if (s.isSkinnedMesh) s.skeleton.bones.forEach(b => { let p: T.Object3D | null = b; while (p && p !== remote) p = p.parent; if (!p) foreign++; }); });
    expect(foreign).toBe(0);
    // Unchanged wheels do not solve again; spin alone does not either.
    wheels.forEach(w => w.rotation += 1);
    applyTrophyRig(remote, wheels);
    expect(trophyRigSolves(remote)).toBe(1);
  });
});
