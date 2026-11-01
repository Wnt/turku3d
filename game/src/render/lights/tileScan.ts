// Reads one draft tile's visual model for street lights: the lamps the generator builds as geometry (tunnel light
// strips, OSM lamps on bridge decks) and, for the lamp pools, a grid of the building footprints. Only those object
// ranges are walked; every other light comes from the tile's light list (tileLights.ts). Only the tile's own model
// is read, so every client derives identical results from the same tile build.
import type * as THREE from 'three/webgpu';

export interface LightPoint { x: number; y: number; z: number }
export interface ScanResult {
  tunnel: LightPoint[]; bridge: LightPoint[];
  /** Building footprints over the scanned area, when one was given. */
  footprint?: Footprint;
}

/** Building footprints of one tile on a coarse grid (a cell is set when a building surface covers its centre):
 *  lamp pools use it to skip lamps that stand behind a building, since point lights cast no shadow. */
export class Footprint {
  static readonly CELL = 2;
  readonly nx: number; readonly nz: number;
  readonly cells: Uint8Array;
  constructor(readonly x0: number, readonly z0: number, x1: number, z1: number) {
    this.nx = Math.ceil((x1 - x0) / Footprint.CELL); this.nz = Math.ceil((z1 - z0) / Footprint.CELL);
    this.cells = new Uint8Array(this.nx * this.nz);
  }
  get x1() { return this.x0 + this.nx * Footprint.CELL; }
  get z1() { return this.z0 + this.nz * Footprint.CELL; }
  /** True inside a covered cell; false outside the grid. */
  covers(x: number, z: number) {
    const i = Math.floor((x - this.x0) / Footprint.CELL), k = Math.floor((z - this.z0) / Footprint.CELL);
    return i >= 0 && k >= 0 && i < this.nx && k < this.nz && this.cells[k * this.nx + i] === 1;
  }
  /** Mark the cells whose centres lie inside one triangle's XZ projection (`det`: twice its signed XZ area). */
  fill(xs: number[], zs: number[], det: number) {
    const C = Footprint.CELL;
    const i0 = Math.max(0, Math.ceil((Math.min(xs[0], xs[1], xs[2]) - this.x0) / C - 0.5));
    const i1 = Math.min(this.nx - 1, Math.floor((Math.max(xs[0], xs[1], xs[2]) - this.x0) / C - 0.5));
    const k0 = Math.max(0, Math.ceil((Math.min(zs[0], zs[1], zs[2]) - this.z0) / C - 0.5));
    const k1 = Math.min(this.nz - 1, Math.floor((Math.max(zs[0], zs[1], zs[2]) - this.z0) / C - 0.5));
    for (let k = k0; k <= k1; k++) {
      const z = this.z0 + (k + 0.5) * C;
      for (let i = i0; i <= i1; i++) {
        const x = this.x0 + (i + 0.5) * C;
        const a = ((zs[1] - zs[2]) * (x - xs[2]) + (xs[2] - xs[1]) * (z - zs[2])) / det;
        const b = ((zs[2] - zs[0]) * (x - xs[2]) + (xs[0] - xs[2]) * (z - zs[2])) / det;
        if (a >= -1e-6 && b >= -1e-6 && 1 - a - b >= -1e-6) this.cells[k * this.nx + i] = 1;
      }
    }
  }
}

type Kind = 'building' | 'tunnel-light' | 'bridge-light';
type Row = Record<string, unknown>;

/** Classify one object range of a tile mesh; null ranges are not read. Buildings are read only for a footprint. */
export function rangeKind(layer: unknown, row: Row): Kind | null {
  const id = String(row.object_id ?? row.id ?? '');
  if (layer === 'buildings' || layer === 'massing') return 'building';
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

/** Walks the model's light ranges, and with an `area` (x0, z0, x1, z1) its building ranges for the footprint grid;
 *  yields every `slice` triangles so callers can time-slice. */
export function* scanTile(model: THREE.Object3D, slice = 4096, area?: readonly [number, number, number, number]): Generator<void, ScanResult> {
  const footprint = area ? new Footprint(area[0], area[1], area[2], area[3]) : undefined;
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
      if (!kind || (kind === 'building' && !footprint)) continue;
      const start = Math.max(0, Number(row.start) || 0), end = Math.min(total, start + (Number(row.count) || 0));
      const light = kind === 'tunnel-light' ? tunnelLights : kind === 'bridge-light' ? bridgeLights : null;
      for (let i = start; i + 2 < end; i += 3) {
        for (let k = 0; k < 3; k++) {
          const v = index ? index.getX(i + k) : i + k;
          const px = position.getX(v), py = position.getY(v), pz = position.getZ(v);
          xs[k] = e[0] * px + e[4] * py + e[8] * pz + e[12];
          ys[k] = e[1] * px + e[5] * py + e[9] * pz + e[13];
          zs[k] = e[2] * px + e[6] * py + e[10] * pz + e[14];
        }
        if (++work % slice === 0) yield;
        if (light) {
          const ux = xs[1] - xs[0], uy = ys[1] - ys[0], uz = zs[1] - zs[0], vx = xs[2] - xs[0], vy = ys[2] - ys[0], vz = zs[2] - zs[0];
          const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
          light.add(xs, ys, zs, ny / (Math.hypot(nx, ny, nz) || 1));
          continue;
        }
        // Twice the XZ-projected signed area; vertical faces cover no cell.
        const det = (zs[1] - zs[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (zs[0] - zs[2]);
        if (Math.abs(det) >= 1e-6) footprint?.fill(xs, zs, det);
      }
    }
  }
  return { tunnel: tunnelLights.list.map(centre), bridge: mergeHeads(bridgeLights.list.filter(isDiffuser).map(centre)), footprint };
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
export function scanTileNow(model: THREE.Object3D, area?: readonly [number, number, number, number]): ScanResult {
  const job = scanTile(model, undefined, area);
  for (;;) { const step = job.next(); if (step.done) return step.value; }
}
