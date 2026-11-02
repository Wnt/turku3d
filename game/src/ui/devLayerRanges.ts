import * as THREE from 'three/webgpu';

interface Row { start: number; count: number }

/** One stable draw per semantic/material pair. Toggle only material visibility, never GPU buffers.
 * The private geometry owns its attribute copies: r186 does not reference-count shared attributes. */
export class DevLayerRanges {
  readonly original: THREE.BufferGeometry;
  readonly geometry: THREE.BufferGeometry;
  private sourceIndex: THREE.BufferAttribute | null;
  private sourceVersion: number;
  private range: { start: number; count: number };
  private materials: { source: THREE.Material; material: THREE.Material; path: string | null }[] = [];
  private drawMaterials: THREE.Material[] = [];
  constructor(private mesh: THREE.Mesh, rows: Row[], paths: (string | null)[]) {
    this.original = mesh.geometry;
    this.sourceIndex = this.original.index;
    this.sourceVersion = this.sourceIndex?.version ?? 0;
    this.range = { ...this.original.drawRange };
    this.geometry = this.original.clone();
    this.geometry.clearGroups();
    this.geometry.setDrawRange(0, Infinity);
    const sources = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    const groups = Array.isArray(mesh.material) ? this.original.groups
      : [{ start: 0, count: this.original.index?.count ?? this.original.attributes.position?.count ?? 0, materialIndex: 0 }];
    const buckets = new Map<string, { spans: { start: number; end: number }[]; count: number; source: THREE.Material; path: string | null }>();
    let count = 0;
    rows.forEach((row, i) => {
      for (const group of groups) {
        const source = sources[group.materialIndex ?? 0];
        const start = Math.max(0, row.start, group.start, this.range.start);
        const end = Math.min(row.start + row.count, group.start + group.count, this.range.start + this.range.count,
          this.original.index?.count ?? this.original.attributes.position?.count ?? 0);
        if (!source || end <= start) continue;
        const key = `${source.uuid}/${paths[i]}`;
        let bucket = buckets.get(key);
        if (!bucket) { bucket = { spans: [], count: 0, source, path: paths[i] }; buckets.set(key, bucket); }
        bucket.spans.push({ start, end });
        bucket.count += end - start; count += end - start;
      }
    });
    // Reuse the private index clone when all ranges are covered. Clipped ranges get
    // one exact-size typed allocation, with no per-triangle JavaScript number arrays.
    const Index = this.original.index?.array instanceof Uint32Array || (!this.original.index
      && (this.original.attributes.position?.count ?? 0) > 65536) ? Uint32Array : Uint16Array;
    const indices = this.geometry.index?.array.length === count ? this.geometry.index.array : new Index(count);
    let offset = 0;
    for (const bucket of buckets.values()) {
      this.geometry.addGroup(offset, bucket.count, this.materials.length);
      for (const { start, end } of bucket.spans) for (let j = start; j < end; j++)
        indices[offset++] = this.original.index?.getX(j) ?? j;
      const material = bucket.source.clone();
      this.materials.push({ source: bucket.source, material, path: bucket.path });
      this.drawMaterials.push(material);
    }
    this.geometry.setIndex(new THREE.BufferAttribute(indices, 1));
  }
  get current() {
    return this.original.index === this.sourceIndex && (this.sourceIndex?.version ?? 0) === this.sourceVersion
      && this.original.drawRange.start === this.range.start && this.original.drawRange.count === this.range.count;
  }
  restore() { if (this.mesh.geometry === this.geometry) this.mesh.geometry = this.original; }
  apply(allowed: (path: string) => boolean) {
    this.mesh.geometry = this.geometry;
    for (const slot of this.materials) slot.material.visible = slot.source.visible && (!slot.path || allowed(slot.path));
    this.mesh.material = this.drawMaterials;
  }
  dispose() {
    this.restore();
    // Material disposal releases every colour/depth/shadow render object before its buffers.
    for (const slot of this.materials) slot.material.dispose();
    this.geometry.dispose();
  }
}
