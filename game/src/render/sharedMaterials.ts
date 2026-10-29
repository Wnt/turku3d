import { StyledMaterials } from './styledMaterials';
import type * as THREE from 'three/webgpu';

/** Exact untextured PBR values share a material across tiles, without merging authored texture/TSL graphs. */
export class SharedMaterials {
  readonly styled = new StyledMaterials();
  private entries = new Map<string, { material: THREE.Material; users: number }>();
  private keys = new Map<THREE.Material, string>();
  private owners = new WeakMap<THREE.Object3D, Set<THREE.Material>>();

  acquire(material: THREE.Material, owner?: THREE.Object3D): THREE.Material {
    if (!(material as THREE.MeshStandardMaterial).isMeshStandardMaterial ||
        Object.values(material).some(value => value?.isTexture || value?.isNode)) return material;
    const json = material.toJSON() as unknown as Record<string, unknown>;
    for (const key of ['uuid', 'metadata']) delete json[key];
    const key = JSON.stringify(json);
    let entry = this.entries.get(key);
    if (!entry) {
      entry = { material, users: 0 };
      this.entries.set(key, entry); this.keys.set(material, key);
    } else if (material !== entry.material) material.dispose();
    if (owner) {
      let owned = this.owners.get(owner);
      if (!owned) { owned = new Set(); this.owners.set(owner, owned); }
      if (owned.has(entry.material)) return entry.material;
      owned.add(entry.material);
    }
    entry.users++;
    return entry.material;
  }

  release(material: THREE.Material) {
    const key = this.keys.get(material);
    if (key === undefined) { material.dispose(); return; }
    const entry = this.entries.get(key)!;
    if (--entry.users) return;
    this.entries.delete(key); this.keys.delete(material); material.dispose();
  }

  releaseModel(owner: THREE.Object3D, materials: Set<THREE.Material>) {
    const owned = this.owners.get(owner);
    for (const material of materials) if (!owned?.has(material) && !this.styled.has(material)) material.dispose();
    for (const material of owned ?? []) this.release(material);
    this.owners.delete(owner);
    this.styled.release(owner);
  }
}

/** Geometry disposal only invalidates r186's attributes; the object event releases render objects/bindings. */
export function disposeDraw(mesh: THREE.Mesh) {
  mesh.dispatchEvent({ type: 'dispose' } as never);
}
