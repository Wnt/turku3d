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
export interface ScanResult { probes: Probe[]; tunnel: LightPoint[]; bridge: LightPoint[] }

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

export interface Cluster { minX: number; minY: number; minZ: number; maxX: number; maxY: number; maxZ: number; up: boolean; down: boolean; cx: number; cz: number; n: number }

/** Greedy clustering of light triangles by centroid; one light per cluster (a multi-head lamp gives one corona). */
class Clusters {
  readonly list: Cluster[] = [];
  private grid = new Map<number, Cluster[]>();
  constructor(private radius = 0.6) {}
  add(xs: number[], ys: number[], zs: number[], ny: number) {
    const cx = (xs[0] + xs[1] + xs[2]) / 3, cz = (zs[0] + zs[1] + zs[2]) / 3;
    const ix = Math.floor(cx / this.radius), iz = Math.floor(cz / this.radius);
    let found: Cluster | null = null;
    for (let dx = -1; dx <= 1 && !found; dx++) for (let dz = -1; dz <= 1 && !found; dz++) {
      for (const c of this.grid.get(cellKey(ix + dx, iz + dz)) ?? []) {
        if (Math.hypot(c.cx - cx, c.cz - cz) <= this.radius) { found = c; break; }
      }
    }
    if (!found) {
      found = { minX: Infinity, minY: Infinity, minZ: Infinity, maxX: -Infinity, maxY: -Infinity, maxZ: -Infinity, up: false, down: false, cx, cz, n: 0 };
      this.list.push(found);
      const key = cellKey(ix, iz);
      const bucket = this.grid.get(key) ?? [];
      bucket.push(found); this.grid.set(key, bucket);
    }
    for (let i = 0; i < 3; i++) {
      found.minX = Math.min(found.minX, xs[i]); found.maxX = Math.max(found.maxX, xs[i]);
      found.minY = Math.min(found.minY, ys[i]); found.maxY = Math.max(found.maxY, ys[i]);
      found.minZ = Math.min(found.minZ, zs[i]); found.maxZ = Math.max(found.maxZ, zs[i]);
    }
    if (ny > 0.5) found.up = true;
    if (ny < -0.5) found.down = true;
    found.n++;
  }
}

const centre = (c: Cluster): LightPoint => ({ x: (c.minX + c.maxX) / 2, y: c.minY - 0.05, z: (c.minZ + c.maxZ) / 2 });

/** A bridge lamp diffuser is a thin, small, closed box (top and bottom faces); rails, posts and deck paint are not. */
export function isDiffuser(c: Cluster) {
  const w = c.maxX - c.minX, d = c.maxZ - c.minZ, h = c.maxY - c.minY;
  return h < 0.05 && Math.max(w, d) <= 0.9 && Math.max(w, d) >= 0.12 && c.up && c.down;
}

/** Scans the model's triangles against the probe points; yields every `slice` triangles so callers can time-slice. */
export function* scanTile(model: THREE.Object3D, points: readonly { x: number; z: number }[], slice = 4096): Generator<void, ScanResult> {
  const probes: Probe[] = points.map(p => ({ x: p.x, z: p.z, ground: -Infinity, water: -Infinity, building: false, tunnel: false, decks: [] }));
  const grid = new Map<number, number[]>();
  let minX = Infinity, minZ = Infinity, maxX = -Infinity, maxZ = -Infinity;
  probes.forEach((p, i) => {
    const key = cellKey(Math.floor(p.x / CELL), Math.floor(p.z / CELL));
    const bucket = grid.get(key) ?? [];
    bucket.push(i); grid.set(key, bucket);
    minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x); minZ = Math.min(minZ, p.z); maxZ = Math.max(maxZ, p.z);
  });
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
      const kind = rangeKind(row.layer ?? mesh.userData.layer, row, materialKey);
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
        // XZ-projected signed area; the y component of the face normal follows its sign.
        const det = (zs[1] - zs[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (zs[0] - zs[2]);
        if (light) {
          const ux = xs[1] - xs[0], uy = ys[1] - ys[0], uz = zs[1] - zs[0], vx = xs[2] - xs[0], vy = ys[2] - ys[0], vz = zs[2] - zs[0];
          const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
          light.add(xs, ys, zs, ny / (Math.hypot(nx, ny, nz) || 1));
          continue;
        }
        if (Math.abs(det) < 1e-6) continue;
        const tx0 = Math.min(xs[0], xs[1], xs[2]), tx1 = Math.max(xs[0], xs[1], xs[2]);
        const tz0 = Math.min(zs[0], zs[1], zs[2]), tz1 = Math.max(zs[0], zs[1], zs[2]);
        if (tx1 < minX || tx0 > maxX || tz1 < minZ || tz0 > maxZ) continue;
        for (let cx = Math.floor(tx0 / CELL); cx <= Math.floor(tx1 / CELL); cx++) {
          for (let cz = Math.floor(tz0 / CELL); cz <= Math.floor(tz1 / CELL); cz++) {
            const bucket = grid.get(cellKey(cx, cz));
            if (!bucket) continue;
            for (const p of bucket) {
              const probe = probes[p];
              const a = ((zs[1] - zs[2]) * (probe.x - xs[2]) + (xs[2] - xs[1]) * (probe.z - zs[2])) / det;
              const b = ((zs[2] - zs[0]) * (probe.x - xs[2]) + (xs[0] - xs[2]) * (probe.z - zs[2])) / det;
              const c = 1 - a - b;
              if (a < -1e-6 || b < -1e-6 || c < -1e-6) continue;
              const y = a * ys[0] + b * ys[1] + c * ys[2];
              if (kind === 'ground') probe.ground = Math.max(probe.ground, y);
              else if (kind === 'water') probe.water = Math.max(probe.water, y);
              else if (kind === 'building') probe.building = true;
              else if (kind === 'tunnel') probe.tunnel = true;
              else probe.decks.push(y);
            }
          }
        }
      }
    }
  }
  return { probes, tunnel: tunnelLights.list.map(centre), bridge: bridgeLights.list.filter(isDiffuser).map(centre) };
}

/** Runs a scan to completion (tests, synchronous callers). */
export function scanTileNow(model: THREE.Object3D, points: readonly { x: number; z: number }[]): ScanResult {
  const job = scanTile(model, points);
  for (;;) { const step = job.next(); if (step.done) return step.value; }
}
