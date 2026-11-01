// Reads one draft tile's visual model for street lights: surface heights and blockers under lamp kerb points,
// plus the lamps the generator already built (tunnel light strips, OSM lamps on bridge decks).
// Only the tile's own model is read, so every client derives identical results from the same tile build.
import type * as THREE from 'three/webgpu';

export interface Probe {
  x: number; z: number;
  /** Highest non-water ground surface, -Infinity when none covers the point. */
  ground: number;
  water: number;
  building: boolean;
  tunnel: boolean;
  /** Heights of bridge deck surfaces covering the point. */
  decks: number[];
}

export interface LightPoint { x: number; y: number; z: number }
export interface ScanResult {
  probes: Probe[];
  /** Per facade probe: inside a building footprint. */
  walls: boolean[];
  tunnel: LightPoint[]; bridge: LightPoint[];
}

type Kind = 'ground' | 'water' | 'building' | 'tunnel' | 'deck' | 'tunnel-light' | 'bridge-light';
type Row = Record<string, unknown>;

const CELL = 4;
const cellKey = (ix: number, iz: number) => (ix + 32768) * 65536 + (iz + 32768);
const water = (key: unknown) => typeof key === 'string' && /(^|[^a-z])(water|river)([^a-z]|$)/i.test(key);

/** Classify one object range of a tile mesh; null ranges are not read. */
export function rangeKind(layer: unknown, row: Row, materialKey: unknown): Kind | null {
  const id = String(row.object_id ?? row.id ?? '');
  if (layer === 'ground') return water(row.material_key ?? materialKey) ? 'water' : 'ground';
  if (layer === 'buildings' || layer === 'massing') return 'building';
  if (layer === 'structures') {
    if (id.startsWith('tunnel:') && id.endsWith(':lights')) return 'tunnel-light';
    if (id === 'tunnel:ground-edge') return null;
    if (id.startsWith('tunnel:') || id.startsWith('passage:')) return 'tunnel';
    return null;
  }
  if (layer === 'bridges') {
    if (/:deck(:|$)/.test(id)) return 'deck';
    // Bridge furniture groups merge parts by surface; lamp diffusers share "paint" with railings and markings.
    if (id.includes(':furniture:paint')) return 'bridge-light';
  }
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

/** Probe points bucketed by 4 m cell, with their overall bounds for a quick triangle reject. */
class ProbeGrid {
  readonly cells = new Map<number, number[]>();
  minX = Infinity; minZ = Infinity; maxX = -Infinity; maxZ = -Infinity;
  constructor(readonly points: readonly { x: number; z: number }[]) {
    points.forEach((p, i) => {
      const key = cellKey(Math.floor(p.x / CELL), Math.floor(p.z / CELL));
      const bucket = this.cells.get(key) ?? [];
      bucket.push(i); this.cells.set(key, bucket);
      this.minX = Math.min(this.minX, p.x); this.maxX = Math.max(this.maxX, p.x);
      this.minZ = Math.min(this.minZ, p.z); this.maxZ = Math.max(this.maxZ, p.z);
    });
  }
  /** Calls `hit(index, y)` for every probe inside the triangle's XZ projection (`det`: twice its signed XZ area). */
  visit(xs: number[], ys: number[], zs: number[], det: number, hit: (index: number, y: number) => void) {
    const tx0 = Math.min(xs[0], xs[1], xs[2]), tx1 = Math.max(xs[0], xs[1], xs[2]);
    const tz0 = Math.min(zs[0], zs[1], zs[2]), tz1 = Math.max(zs[0], zs[1], zs[2]);
    if (tx1 < this.minX || tx0 > this.maxX || tz1 < this.minZ || tz0 > this.maxZ) return;
    for (let cx = Math.floor(tx0 / CELL); cx <= Math.floor(tx1 / CELL); cx++) {
      for (let cz = Math.floor(tz0 / CELL); cz <= Math.floor(tz1 / CELL); cz++) {
        const bucket = this.cells.get(cellKey(cx, cz));
        if (!bucket) continue;
        for (const i of bucket) {
          const p = this.points[i];
          const a = ((zs[1] - zs[2]) * (p.x - xs[2]) + (xs[2] - xs[1]) * (p.z - zs[2])) / det;
          const b = ((zs[2] - zs[0]) * (p.x - xs[2]) + (xs[0] - xs[2]) * (p.z - zs[2])) / det;
          const c = 1 - a - b;
          if (a < -1e-6 || b < -1e-6 || c < -1e-6) continue;
          hit(i, a * ys[0] + b * ys[1] + c * ys[2]);
        }
      }
    }
  }
}

/** Scans the model's triangles against the probe points; yields every `slice` triangles so callers can time-slice.
 *  `walls` are facade-search probes, tested against building footprints only. */
export function* scanTile(model: THREE.Object3D, points: readonly { x: number; z: number }[],
  walls: readonly { x: number; z: number }[] = [], slice = 4096): Generator<void, ScanResult> {
  const probes: Probe[] = points.map(p => ({ x: p.x, z: p.z, ground: -Infinity, water: -Infinity, building: false, tunnel: false, decks: [] }));
  const covered = walls.map(() => false);
  const surface = new ProbeGrid(probes), facades = new ProbeGrid(walls);
  let kind: Kind | null = null;
  const onSurface = (i: number, y: number) => {
    const probe = probes[i];
    if (kind === 'ground') probe.ground = Math.max(probe.ground, y);
    else if (kind === 'water') probe.water = Math.max(probe.water, y);
    else if (kind === 'building') probe.building = true;
    else if (kind === 'tunnel') probe.tunnel = true;
    else probe.decks.push(y);
  };
  const onFacade = (i: number) => { covered[i] = true; };
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
    const material = Array.isArray(mesh.material) ? mesh.material[0] : mesh.material;
    const materialKey = mesh.userData.material_key ?? material?.userData?.material_key;
    const e = mesh.matrixWorld.elements;
    for (const row of rows) {
      kind = rangeKind(row.layer ?? mesh.userData.layer, row, materialKey);
      if (!kind) continue;
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
        // Twice the XZ-projected signed area; vertical faces cover no point.
        const det = (zs[1] - zs[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (zs[0] - zs[2]);
        if (Math.abs(det) < 1e-6) continue;
        surface.visit(xs, ys, zs, det, onSurface);
        if (kind === 'building') facades.visit(xs, ys, zs, det, onFacade);
      }
    }
  }
  return { probes, walls: covered, tunnel: tunnelLights.list.map(centre), bridge: mergeHeads(bridgeLights.list.filter(isDiffuser).map(centre)) };
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
export function scanTileNow(model: THREE.Object3D, points: readonly { x: number; z: number }[], walls: readonly { x: number; z: number }[] = []): ScanResult {
  const job = scanTile(model, points, walls);
  for (;;) { const step = job.next(); if (step.done) return step.value; }
}
