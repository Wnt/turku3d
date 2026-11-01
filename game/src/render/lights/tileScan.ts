// Reads the lamps the generator builds as geometry from one draft tile's visual model: tunnel light strips and
// OSM lamps on bridge decks. Only those object ranges are walked, so the scan is small; every other light comes
// from the tile's light list (tileLights.ts). Only the tile's own model is read, so every client agrees.
import type * as THREE from 'three/webgpu';

export interface LightPoint { x: number; y: number; z: number }
export interface ScanResult { tunnel: LightPoint[]; bridge: LightPoint[] }

type Kind = 'tunnel-light' | 'bridge-light';
type Row = Record<string, unknown>;

/** Classify one object range of a tile mesh; null ranges are not read. */
export function rangeKind(layer: unknown, row: Row): Kind | null {
  const id = String(row.object_id ?? row.id ?? '');
  if (layer === 'structures' && id.startsWith('tunnel:') && id.endsWith(':lights')) return 'tunnel-light';
  // Bridge furniture groups merge parts by surface; lamp diffusers share "paint" with railings and markings.
  if (layer === 'bridges' && id.includes(':furniture:paint')) return 'bridge-light';
  return null;
}

export interface Cluster { minX: number; minY: number; minZ: number; maxX: number; maxY: number; maxZ: number; up: boolean; down: boolean }

/** Connected parts of light triangles, welded by vertex position (1 mm): one head box, one tunnel strip, one rail. */
class Clusters {
  private parent: number[] = [];
  private ids = new Map<string, number>();
  private triangles: { v: number; xs: number[]; ys: number[]; zs: number[]; ny: number }[] = [];
  private vertex(x: number, y: number, z: number) {
    const key = `${Math.round(x * 1000)},${Math.round(y * 1000)},${Math.round(z * 1000)}`;
    let id = this.ids.get(key);
    if (id === undefined) { id = this.parent.length; this.parent.push(id); this.ids.set(key, id); }
    return id;
  }
  private root(i: number): number {
    while (this.parent[i] !== i) { this.parent[i] = this.parent[this.parent[i]]; i = this.parent[i]; }
    return i;
  }
  add(xs: number[], ys: number[], zs: number[], ny: number) {
    const v = [0, 1, 2].map(k => this.vertex(xs[k], ys[k], zs[k]));
    for (const other of v.slice(1)) { const a = this.root(v[0]), b = this.root(other); if (a !== b) this.parent[b] = a; }
    this.triangles.push({ v: v[0], xs: [...xs], ys: [...ys], zs: [...zs], ny });
  }
  get list(): Cluster[] {
    const parts = new Map<number, Cluster>();
    for (const t of this.triangles) {
      const r = this.root(t.v);
      let c = parts.get(r);
      if (!c) { c = { minX: Infinity, minY: Infinity, minZ: Infinity, maxX: -Infinity, maxY: -Infinity, maxZ: -Infinity, up: false, down: false }; parts.set(r, c); }
      for (let i = 0; i < 3; i++) {
        c.minX = Math.min(c.minX, t.xs[i]); c.maxX = Math.max(c.maxX, t.xs[i]);
        c.minY = Math.min(c.minY, t.ys[i]); c.maxY = Math.max(c.maxY, t.ys[i]);
        c.minZ = Math.min(c.minZ, t.zs[i]); c.maxZ = Math.max(c.maxZ, t.zs[i]);
      }
      if (t.ny > 0.5) c.up = true;
      if (t.ny < -0.5) c.down = true;
    }
    return [...parts.values()];
  }
}

const centre = (c: Cluster): LightPoint => ({ x: (c.minX + c.maxX) / 2, y: c.minY - 0.05, z: (c.minZ + c.maxZ) / 2 });

/** A bridge lamp diffuser is a thin, small, closed box (top and bottom faces); rails, posts and deck paint are not. */
export function isDiffuser(c: Cluster) {
  const w = c.maxX - c.minX, d = c.maxZ - c.minZ, h = c.maxY - c.minY;
  return h < 0.05 && Math.max(w, d) <= 0.9 && Math.max(w, d) >= 0.12 && c.up && c.down;
}

/** Walks the model's light ranges; yields every `slice` triangles so callers can time-slice. */
export function* scanTile(model: THREE.Object3D, slice = 4096): Generator<void, ScanResult> {
  const tunnelLights = new Clusters(), bridgeLights = new Clusters();
  const meshes: THREE.Mesh[] = [];
  model.updateMatrixWorld(true);
  model.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (mesh.isMesh && !(mesh as THREE.InstancedMesh).isInstancedMesh && !mesh.userData.shadowBatch && mesh.geometry?.getAttribute('position'))
      meshes.push(mesh);
  });
  const xs = [0, 0, 0], ys = [0, 0, 0], zs = [0, 0, 0];
  let work = 0;
  for (const mesh of meshes) {
    const geometry = mesh.geometry, index = geometry.getIndex(), position = geometry.getAttribute('position');
    const total = index ? index.count : position.count;
    const rows = Array.isArray(mesh.userData.objects) ? mesh.userData.objects as Row[] : [{ ...mesh.userData, start: 0, count: total }];
    const e = mesh.matrixWorld.elements;
    for (const row of rows) {
      const kind = rangeKind(row.layer ?? mesh.userData.layer, row);
      if (!kind) continue;
      const start = Math.max(0, Number(row.start) || 0), end = Math.min(total, start + (Number(row.count) || 0));
      const light = kind === 'tunnel-light' ? tunnelLights : bridgeLights;
      for (let i = start; i + 2 < end; i += 3) {
        for (let k = 0; k < 3; k++) {
          const v = index ? index.getX(i + k) : i + k;
          const px = position.getX(v), py = position.getY(v), pz = position.getZ(v);
          xs[k] = e[0] * px + e[4] * py + e[8] * pz + e[12];
          ys[k] = e[1] * px + e[5] * py + e[9] * pz + e[13];
          zs[k] = e[2] * px + e[6] * py + e[10] * pz + e[14];
        }
        if (++work % slice === 0) yield;
        const ux = xs[1] - xs[0], uy = ys[1] - ys[0], uz = zs[1] - zs[0], vx = xs[2] - xs[0], vy = ys[2] - ys[0], vz = zs[2] - zs[0];
        const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
        light.add(xs, ys, zs, ny / (Math.hypot(nx, ny, nz) || 1));
      }
    }
  }
  return { tunnel: tunnelLights.list.map(centre), bridge: mergeHeads(bridgeLights.list.filter(isDiffuser).map(centre)) };
}

/** Heads of one multi-head lamp (OSM light:count) sit within about 1.1 m of each other: one corona per lamp. */
export function mergeHeads(points: readonly LightPoint[], radius = 1.3): LightPoint[] {
  const groups: LightPoint[][] = [];
  for (const p of points) {
    const group = groups.find(g => Math.hypot(g[0].x - p.x, g[0].z - p.z) <= radius && Math.abs(g[0].y - p.y) < 0.5);
    if (group) group.push(p); else groups.push([p]);
  }
  return groups.map(g => ({ x: g.reduce((a, p) => a + p.x, 0) / g.length, y: g.reduce((a, p) => a + p.y, 0) / g.length,
    z: g.reduce((a, p) => a + p.z, 0) / g.length }));
}

/** Runs a scan to completion (tests, synchronous callers). */
export function scanTileNow(model: THREE.Object3D): ScanResult {
  const job = scanTile(model);
  for (;;) { const step = job.next(); if (step.done) return step.value; }
}
