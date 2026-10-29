import type * as THREE from 'three/webgpu';

/** Cache graphs before construction. Texture UUIDs are the final canonical sampler/UV variants,
 * not URLs or material names: authored facades with different maps must remain different. */
export class StyledMaterials {
  private entries = new Map<string, { material: THREE.Material; owners: number }>();
  private managed = new WeakSet<THREE.Material>();
  has(material: THREE.Material) { return this.managed.has(material); }
  private owners = new WeakMap<THREE.Object3D, Set<string>>();
  acquire(source: THREE.Material, variant: string, owner: THREE.Object3D, create: () => THREE.Material): THREE.Material {
    // Never serialize an arbitrary authored TSL graph or combine its object-specific uniforms.
    if ((source as THREE.NodeMaterial).isNodeMaterial) return source;
    const json = source.toJSON({ textures: {}, images: {}, geometries: {}, materials: {}, shapes: {}, skeletons: {}, animations: {}, nodes: {} }) as unknown as Record<string, unknown>;
    delete json.uuid; delete json.metadata;
    const key = `${variant}:${JSON.stringify(json)}`;
    let entry = this.entries.get(key);
    if (!entry) { entry = { material: create(), owners: 0 }; this.entries.set(key, entry); this.managed.add(entry.material); }
    let owned = this.owners.get(owner);
    if (!owned) { owned = new Set(); this.owners.set(owner, owned); }
    if (!owned.has(key)) { owned.add(key); entry.owners++; }
    return entry.material;
  }
  release(owner: THREE.Object3D) {
    for (const key of this.owners.get(owner) ?? []) {
      const entry = this.entries.get(key)!;
      if (--entry.owners === 0) { entry.material.dispose(); this.entries.delete(key); }
    }
    this.owners.delete(owner);
  }
}
