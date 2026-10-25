// Geometry helpers: seeded RNG, GeoJSON footprints/curb lines in engine [x, z], point-in-polygon.

export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export type Ring = [number, number][]; // [x, z], closed or open
export interface Footprint {
  id: string;
  rings: Ring[]; // [outer, ...holes]
  bbox: [number, number, number, number]; // x0 z0 x1 z1
  area: number;
}

const E0 = 23459500, N0 = 6704500;

/**
 * GeoJSON coordinates convention: engine world-local [x, z] (default, or
 * `"turku3d_coords": "engine-xz"`), or EPSG:3877 [E, N] with `"turku3d_coords": "en"`
 * (converted with x = E - E0, z = -(N - N0)).
 */
function convertor(fc: any): (c: number[]) => [number, number] {
  const mode = fc.turku3d_coords ?? 'engine-xz';
  if (mode === 'engine-xz') return (c) => [c[0], c[1]];
  if (mode === 'en') return (c) => [c[0] - E0, -(c[1] - N0)];
  throw new Error(`unknown turku3d_coords ${mode}`);
}

function ringArea(r: Ring): number {
  let a = 0;
  for (let i = 0, n = r.length; i < n; i++) {
    const [x0, z0] = r[i], [x1, z1] = r[(i + 1) % n];
    a += x0 * z1 - x1 * z0;
  }
  return a / 2;
}

function openRing(r: Ring): Ring {
  const n = r.length;
  if (n > 1 && r[0][0] === r[n - 1][0] && r[0][1] === r[n - 1][1]) return r.slice(0, n - 1);
  return r;
}

export function loadFootprints(fc: any): Footprint[] {
  const cv = convertor(fc);
  const out: Footprint[] = [];
  let k = 0;
  for (const f of fc.features ?? []) {
    const g = f.geometry;
    if (!g) continue;
    const polys = g.type === 'Polygon' ? [g.coordinates] : g.type === 'MultiPolygon' ? g.coordinates : [];
    for (const p of polys) {
      const rings = (p as number[][][]).map((r) => openRing(r.map(cv)));
      let x0 = Infinity, z0 = Infinity, x1 = -Infinity, z1 = -Infinity;
      for (const [x, z] of rings[0]) { x0 = Math.min(x0, x); z0 = Math.min(z0, z); x1 = Math.max(x1, x); z1 = Math.max(z1, z); }
      const area = Math.abs(ringArea(rings[0])) - rings.slice(1).reduce((s, r) => s + Math.abs(ringArea(r)), 0);
      out.push({ id: String(f.properties?.id ?? f.id ?? `fp${k}`), rings, bbox: [x0, z0, x1, z1], area });
      k++;
    }
  }
  return out;
}

interface CurbLine { id: string; points: [number, number][]; }

export function loadLines(fc: any): CurbLine[] {
  const cv = convertor(fc);
  const out: CurbLine[] = [];
  let k = 0;
  for (const f of fc.features ?? []) {
    const g = f.geometry;
    if (!g) continue;
    const lines = g.type === 'LineString' ? [g.coordinates] : g.type === 'MultiLineString' ? g.coordinates : [];
    for (const l of lines) out.push({ id: String(f.properties?.id ?? f.id ?? `line${k++}`), points: (l as number[][]).map(cv) });
  }
  return out;
}

function inRing(r: Ring, x: number, z: number): boolean {
  let inside = false;
  for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
    const [xi, zi] = r[i], [xj, zj] = r[j];
    if (zi > z !== zj > z && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) inside = !inside;
  }
  return inside;
}

function distToRing(r: Ring, x: number, z: number): number {
  let d = Infinity;
  for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
    const [ax, az] = r[j], [bx, bz] = r[i];
    const ex = bx - ax, ez = bz - az;
    const L = ex * ex + ez * ez;
    const t = L > 0 ? Math.max(0, Math.min(1, ((x - ax) * ex + (z - az) * ez) / L)) : 0;
    d = Math.min(d, Math.hypot(ax + t * ex - x, az + t * ez - z));
  }
  return d;
}

/** Signed depth inside the footprint (> 0 inside, < 0 outside), in metres. */
function footprintDepth(f: Footprint, x: number, z: number): number {
  const [x0, z0, x1, z1] = f.bbox;
  const d = Math.min(...f.rings.map((r) => distToRing(r, x, z)));
  if (x < x0 || x > x1 || z < z0 || z > z1) return -d;
  const inside = inRing(f.rings[0], x, z) && !f.rings.slice(1).some((r) => inRing(r, x, z));
  return inside ? d : -d;
}

export class FootprintIndex {
  cell: number;
  grid = new Map<string, Footprint[]>();
  all: Footprint[];
  constructor(fps: Footprint[], cell = 10) {
    this.cell = cell;
    this.all = fps;
    for (const f of fps) {
      const [x0, z0, x1, z1] = f.bbox;
      for (let i = Math.floor(x0 / cell); i <= Math.floor(x1 / cell); i++)
        for (let j = Math.floor(z0 / cell); j <= Math.floor(z1 / cell); j++) {
          const k = `${i},${j}`;
          if (!this.grid.has(k)) this.grid.set(k, []);
          this.grid.get(k)!.push(f);
        }
    }
  }
  near(x: number, z: number): Footprint[] {
    return this.grid.get(`${Math.floor(x / this.cell)},${Math.floor(z / this.cell)}`) ?? [];
  }
  /** Footprint containing (x, z) deeper than `tol`, if any. */
  containing(x: number, z: number, tol = 0): Footprint | undefined {
    for (const f of this.near(x, z)) if (footprintDepth(f, x, z) > tol) return f;
    return undefined;
  }
}

export function ringOrientation(r: Ring): number {
  return Math.sign(ringArea(r));
}
