import type * as THREE from 'three/webgpu';

/** Derived geometry owns a bounded idle LRU, independently of tile lifetimes. */
export class PrototypeGeometry {
  private entries = new Map<string, { geometry: THREE.BufferGeometry; users: number }>();
  private keys = new Map<THREE.BufferGeometry, string>();
  private signatures = new WeakMap<THREE.BufferGeometry, string>();
  constructor(private idleLimit = 64) {}

  /** Hash decoded prototype values, never placement, UUID or tile identity. */
  signature(geometry: THREE.BufferGeometry): string {
    const cached = this.signatures.get(geometry);
    if (cached) return cached;
    let a = 2166136261, b = 5381;
    const word = new Float32Array(1), bits = new Uint32Array(word.buffer);
    const hash = (value: number) => {
      word[0] = value;
      a = Math.imul(a ^ bits[0], 16777619);
      b = Math.imul(b, 33) ^ bits[0];
    };
    const layout: string[] = [];
    for (const name of ['position', 'normal', 'color', 'index']) {
      const attribute = name === 'index' ? geometry.index : geometry.getAttribute(name);
      layout.push(`${name}:${attribute?.count ?? 0}:${attribute?.itemSize ?? 0}`);
      if (!attribute) continue;
      for (let i = 0; i < attribute.count; i++) {
        hash(attribute.getX(i));
        if (attribute.itemSize > 1) hash(attribute.getY(i));
        if (attribute.itemSize > 2) hash(attribute.getZ(i));
        if (attribute.itemSize > 3) hash(attribute.getW(i));
      }
    }
    const signature = `${layout.join('/')}/${a >>> 0}/${b >>> 0}`;
    this.signatures.set(geometry, signature);
    return signature;
  }

  acquire(key: string, build: () => THREE.BufferGeometry): THREE.BufferGeometry {
    let entry = this.entries.get(key);
    if (!entry) {
      entry = { geometry: build(), users: 0 };
      this.keys.set(entry.geometry, key);
    }
    entry.users++;
    this.entries.delete(key); this.entries.set(key, entry);
    return entry.geometry;
  }

  release(geometry: THREE.BufferGeometry) {
    const key = this.keys.get(geometry)!;
    const entry = this.entries.get(key)!;
    entry.users--;
    this.entries.delete(key); this.entries.set(key, entry);
    let idle = [...this.entries.values()].filter(value => value.users === 0).length;
    for (const [id, value] of this.entries) {
      if (idle <= this.idleLimit) break;
      if (value.users) continue;
      value.geometry.dispose(); this.keys.delete(value.geometry); this.entries.delete(id); idle--;
    }
  }
}
