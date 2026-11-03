import { updateStaticBatchVisibility } from './staticBatch';
import * as THREE from 'three/webgpu';
import { shareModelTextures } from './sharedAssets';
import { retainGeometry } from './modelGeometry';
import { PreparationBudget } from '../render/preparationBudget';

type Vec3 = [number, number, number];
export interface BuildingModel {
  id: string; uri: string; bounds: { min: Vec3; max: Vec3 };
  /** Conservative opaque volumes, never inferred from display bounds. Owned by perfcpu. */
  occluders?: unknown;
}
export interface BuildingPlacements {
  version: 1;
  models: BuildingModel[];
  placements: { id: string; model: string; position: Vec3; viewDistance: number }[];
}

export function validateBuildingPlacements(data: BuildingPlacements) {
  if (data.version !== 1 || !Array.isArray(data.models) || !Array.isArray(data.placements))
    throw new Error('Unsupported building placements');
  const vector = (v: number[]) => Array.isArray(v) && v.length === 3 && v.every(Number.isFinite);
  const models = new Set<string>(), ids = new Set<string>();
  for (const model of data.models) {
    if (!/^[a-f0-9]{64}$/.test(model.id) || models.has(model.id) || typeof model.uri !== 'string'
      || !model.bounds || !vector(model.bounds.min) || !vector(model.bounds.max)
      || model.bounds.min.some((v, i) => v > model.bounds.max[i])) throw new Error('Invalid building model');
    models.add(model.id);
  }
  for (const placement of data.placements) {
    if (typeof placement.id !== 'string' || ids.has(placement.id) || !models.has(placement.model)
      || !vector(placement.position) || !Number.isFinite(placement.viewDistance) || placement.viewDistance <= 0)
      throw new Error('Invalid building placement');
    ids.add(placement.id);
  }
}

interface Resident { users: number; promise: Promise<THREE.Object3D> }
interface Lease { root: THREE.Object3D; release: () => void }

/** Decode each content model once while resident. Tile cancellation releases every successful lease. */
export class BuildingModelCache {
  private entries = new Map<string, Resident>();
  private active = 0;
  private queue: (() => void)[] = [];
  constructor(private load: (url: string) => Promise<THREE.Object3D>,
    private dispose: (root: THREE.Object3D) => void, private budget = new PreparationBudget()) {}

  private async read(url: string) {
    if (this.active >= 4) await new Promise<void>(resolve => this.queue.push(resolve));
    else this.active++;
    try {
      const root = await this.load(url);
      const geometries = new Set<THREE.BufferGeometry>();
      root.traverse(o => { if ((o as THREE.Mesh).isMesh) geometries.add((o as THREE.Mesh).geometry); });
      for (const geometry of geometries) retainGeometry(geometry);
      return root;
    } finally {
      const next = this.queue.shift();
      if (next) next(); else this.active--;
    }
  }

  private async acquire(model: BuildingModel, base: URL): Promise<Lease> {
    // Hashes are reusable across generation URL prefixes, but never across content hosts.
    const url = new URL(model.uri, base), key = `${url.origin}/${model.id}`;
    let entry = this.entries.get(key);
    if (!entry) { entry = { users: 0, promise: this.read(url.href) }; this.entries.set(key, entry); }
    entry.users++;
    let released = false;
    const release = () => {
      if (released) return;
      released = true;
      if (--entry.users) return;
      if (this.entries.get(key) === entry) this.entries.delete(key);
      void entry.promise.then(root => this.dispose(root), () => {});
    };
    try { return { root: await entry.promise, release }; }
    catch (error) { release(); throw error; }
  }

  async attach(owner: THREE.Object3D, data: BuildingPlacements, base: URL): Promise<() => void> {
    validateBuildingPlacements(data);
    const descriptors = new Map(data.models.map(model => [model.id, model]));
    const needed = [...new Set(data.placements.map(p => p.model))];
    const results = await Promise.allSettled(needed.map(id => this.acquire(descriptors.get(id)!, base)));
    const leases = results.flatMap(result => result.status === 'fulfilled' ? [result.value] : []);
    const failure = results.find(result => result.status === 'rejected');
    if (failure?.status === 'rejected') { for (const lease of leases) lease.release(); throw failure.reason; }
    const templates = new Map(needed.map((id, i) => [id, leases[i].root]));
    const geometries = new Set<THREE.BufferGeometry>();
    const materials = new Map<THREE.Material, THREE.Material>(), textures = new Map<THREE.Texture, THREE.Texture>();
    for (const placement of data.placements) {
      // Network admission does not bound the clone burst after all model promises resolve.
      let pause = this.budget.checkpoint();
      // Other tile attachments can spend this shared budget when the same yield resolves.
      while (pause) { await pause; pause = this.budget.checkpoint(); }
      const descriptor = descriptors.get(placement.model)!;
      const root = templates.get(placement.model)!.clone(true);
      root.traverse(o => {
        const mesh = o as THREE.Mesh;
        if (!mesh.isMesh) return;
        geometries.add(mesh.geometry);
        const cloneMaterial = (source: THREE.Material) => {
          let material = materials.get(source);
          if (material) return material;
          material = source.clone(); materials.set(source, material);
          for (const [slot, value] of Object.entries(material)) {
            if (!(value as THREE.Texture)?.isTexture) continue;
            const sourceTexture = value as THREE.Texture;
            let texture = textures.get(sourceTexture);
            if (!texture) { texture = sourceTexture.clone(); textures.set(sourceTexture, texture); }
            (material as unknown as Record<string, unknown>)[slot] = texture;
          }
          return material;
        };
        mesh.material = Array.isArray(mesh.material) ? mesh.material.map(cloneMaterial) : cloneMaterial(mesh.material);
      });
      const wrapper = new THREE.Group();
      wrapper.name = `building:${placement.id}`;
      wrapper.position.fromArray(placement.position);
      wrapper.userData = { archetype: placement.model, placementId: placement.id,
        viewDistance: placement.viewDistance, modelBounds: descriptor.bounds, occluders: descriptor.occluders };
      wrapper.add(root); owner.add(wrapper);
    }
    for (const geometry of geometries) retainGeometry(geometry);
    // Each unstyled material owner enters the existing styling cache independently.
    shareModelTextures(owner);
    return () => { for (const lease of leases) lease.release(); };
  }
}

/** Cull complete placements against their own expanded bounds, never their container square. */
export function updateBuildingDistances(root: THREE.Object3D, x: number, z: number) {
  for (const child of root.children) {
    if (!child.userData.archetype) continue;
    const bounds = child.userData.modelBounds as BuildingModel['bounds'];
    const dx = Math.max(child.position.x + bounds.min[0] - x, 0, x - child.position.x - bounds.max[0]);
    const dz = Math.max(child.position.z + bounds.min[2] - z, 0, z - child.position.z - bounds.max[2]);
    child.visible = dx * dx + dz * dz <= child.userData.viewDistance ** 2;
  }
  updateStaticBatchVisibility(root);
}
