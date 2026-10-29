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
    const buckets = new Map<string, { indices: number[]; source: THREE.Material; path: string | null }>();
    rows.forEach((row, i) => {
      for (const group of groups) {
        const source = sources[group.materialIndex ?? 0];
        const start = Math.max(0, row.start, group.start, this.range.start);
        const end = Math.min(row.start + row.count, group.start + group.count, this.range.start + this.range.count,
          this.original.index?.count ?? this.original.attributes.position?.count ?? 0);
        if (!source || end <= start) continue;
        const key = `${source.uuid}/${paths[i]}`;
        let bucket = buckets.get(key);
        if (!bucket) { bucket = { indices: [], source, path: paths[i] }; buckets.set(key, bucket); }
        for (let j = start; j < end; j++) bucket.indices.push(this.original.index?.getX(j) ?? j);
      }
    });
    const indices: number[] = [];
    for (const bucket of buckets.values()) {
      this.geometry.addGroup(indices.length, bucket.indices.length, this.materials.length);
      for (const index of bucket.indices) indices.push(index);
      this.materials.push({ source: bucket.source, material: bucket.source.clone(), path: bucket.path });
    }
    this.geometry.setIndex(indices);
  }
  get current() {
    return this.original.index === this.sourceIndex && (this.sourceIndex?.version ?? 0) === this.sourceVersion
      && this.original.drawRange.start === this.range.start && this.original.drawRange.count === this.range.count;
  }
  restore() { if (this.mesh.geometry === this.geometry) this.mesh.geometry = this.original; }
  apply(allowed: (path: string) => boolean) {
    this.mesh.geometry = this.geometry;
    for (const slot of this.materials) slot.material.visible = slot.source.visible && (!slot.path || allowed(slot.path));
    this.mesh.material = this.materials.map(slot => slot.material);
  }
  dispose() {
    this.restore();
    // Material disposal releases every colour/depth/shadow render object before its buffers.
    for (const slot of this.materials) slot.material.dispose();
    this.geometry.dispose();
  }
}
