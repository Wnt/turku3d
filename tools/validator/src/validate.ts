// Rapier validator for .col collision tiles (ARCHITECTURE Task 1.11).
// Checks: format, holes (rays), buildings (random walkers vs footprints), curbs (step climb/block),
// stairs (sloped colliders up/down), belowGround (drops + every walker trajectory).

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import RAPIER from '@dimforge/rapier3d-compat';
import { MeshoptDecoder } from 'meshoptimizer';
import {
  COL_FLAG, MAT, MATERIALS, MAT_FLAG, SOLID_QUERY_FILTER, buildTileColliders, checkShippable,
  materialHasFlag, readCol,
} from '../../../game/src/shared/col/index.ts';
import type { BuiltTile, ColTile } from '../../../game/src/shared/col/index.ts';
import { DEFAULT_KCC, Walker } from './kcc.ts';
import type { KccConfig } from './kcc.ts';
import { FootprintIndex, loadFootprints, loadLines, mulberry32, ringOrientation } from './geo.ts';
import type { Footprint } from './geo.ts';

export const TILE = 62.5;
export type Status = 'pass' | 'fail' | 'skipped';
export const CHECKS = ['format', 'holes', 'buildings', 'curbs', 'stairs', 'belowGround'] as const;
export type CheckName = (typeof CHECKS)[number];

export interface Options {
  inputs: string[];
  footprints?: string;
  curbs?: string;
  checks?: CheckName[];
  seed: number;
  walkers: number;
  walkSeconds: number;
  rayDensity: number; // rays per m² (spec: 1 ray per 0.5 m² = 2)
  dropSpacing: number;
  curbSpacing: number;
  curbSpeeds: number[];
  climbRequired: number; // steps <= this must be climbed (Task 1.11c: 0.25)
  blockRequired: number; // steps >= this must block (Task 1.6/1.13: 0.35)
  stairMaxSlopeDeg: number;
  kcc: KccConfig;
  aoi?: [number, number, number, number];
  maxSamples: number;
}

export const DEFAULT_OPTIONS: Omit<Options, 'inputs'> = {
  seed: 1,
  walkers: 2000,
  walkSeconds: 10,
  rayDensity: 2,
  dropSpacing: 5,
  curbSpacing: 2,
  curbSpeeds: [1.5, 4.0],
  climbRequired: 0.25,
  blockRequired: 0.35,
  stairMaxSlopeDeg: 40,
  kcc: DEFAULT_KCC,
  maxSamples: 20,
};

interface Loaded {
  file: string;
  bytes: number;
  tile: ColTile;
  built: BuiltTile;
}

const r3 = (v: number) => Math.round(v * 1000) / 1000;

export function collectColFiles(inputs: string[]): string[] {
  const out: string[] = [];
  const walk = (p: string) => {
    const st = statSync(p);
    if (st.isDirectory()) for (const e of readdirSync(p).sort()) walk(join(p, e));
    else if (p.endsWith('.col')) out.push(resolve(p));
  };
  for (const i of inputs) walk(i);
  return out;
}

export async function validate(opts: Options): Promise<any> {
  const t0 = performance.now();
  await RAPIER.init();
  await MeshoptDecoder.ready;
  const want = new Set<CheckName>(opts.checks ?? CHECKS);
  const world = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  const files = collectColFiles(opts.inputs);
  const report: any = {
    validator: 'turku3d-col-validator',
    reportVersion: 1,
    rapier: RAPIER.version(),
    started: new Date().toISOString(),
    options: { ...opts, kcc: { ...opts.kcc } },
    tiles: [] as any[],
    checks: {} as Record<string, any>,
  };
  const loaded: Loaded[] = [];
  const formatErrors: string[] = [];
  for (const f of files) {
    const entry: any = { file: f };
    report.tiles.push(entry);
    let tile: ColTile;
    const buf = readFileSync(f);
    try {
      tile = readCol(buf, { decoder: MeshoptDecoder, verifyCrc: true, onWarning: (m) => formatErrors.push(`${f}: ${m}`) });
    } catch (e: any) {
      entry.error = String(e.message ?? e);
      formatErrors.push(`${f}: ${entry.error}`);
      continue;
    }
    const errs = [...checkShippable(tile), ...tileConsistency(tile)];
    const built = buildTileColliders(RAPIER, world, tile);
    Object.assign(entry, {
      id: tile.id,
      bytes: buf.byteLength,
      vertices: tile.header.vertexCount,
      triangles: tile.header.triangleCount,
      primitives: tile.primitives.length,
      flags: tile.header.flags,
      encodings: Object.fromEntries(tile.sections.map((s) => [s.tag, s.encoding])),
      buildMs: r3(built.buildMs),
      colliders: built.colliders.map((c) => `${c.kind}:${c.cls}`),
      errors: errs,
    });
    for (const e of errs) formatErrors.push(`${tile.id}: ${e}`);
    loaded.push({ file: f, bytes: buf.byteLength, tile, built });
  }
  if (!loaded.length) throw new Error(`no loadable .col tiles in ${opts.inputs.join(', ')}`);
  const ids = loaded.map((l) => l.tile.id);
  const dup = ids.filter((v, i) => ids.indexOf(v) !== i);
  if (dup.length) formatErrors.push(`duplicate tiles ${[...new Set(dup)].join(',')}`);
  world.step(); // update the broad phase so scene queries see every collider

  const ctx = new Ctx(RAPIER, world, loaded, opts);
  report.aoi = ctx.aoiDesc();

  if (want.has('format')) report.checks.format = { status: formatErrors.length ? 'fail' : 'pass', errors: formatErrors };
  if (want.has('holes')) report.checks.holes = ctx.checkHoles();
  if (want.has('buildings')) report.checks.buildings = opts.footprints ? ctx.checkBuildings(loadFootprints(JSON.parse(readFileSync(opts.footprints, 'utf8')))) : { status: 'skipped', reason: 'no --footprints given' };
  if (want.has('curbs')) report.checks.curbs = ctx.checkCurbs(opts.curbs ? JSON.parse(readFileSync(opts.curbs, 'utf8')) : undefined);
  if (want.has('stairs')) report.checks.stairs = ctx.checkStairs();
  if (want.has('belowGround')) report.checks.belowGround = ctx.checkBelowGround();

  ctx.walker.free();
  const statuses = Object.values(report.checks).map((c: any) => c.status as Status);
  report.pass = !statuses.includes('fail');
  report.summary = Object.fromEntries(Object.entries(report.checks).map(([k, v]: [string, any]) => [k, v.status]));
  report.elapsedMs = Math.round(performance.now() - t0);
  return report;
}

function tileConsistency(tile: ColTile): string[] {
  const h = tile.header;
  const errs: string[] = [];
  const [ox, oy, oz] = h.origin;
  if (ox !== h.tileI * TILE || oy !== 0 || oz !== h.tileJ * TILE) errs.push(`origin (${ox},${oy},${oz}) != (i*62.5, 0, j*62.5)`);
  const p = tile.positions;
  const eps = 1e-3;
  for (let t = 0; t < tile.indices.length; t++) {
    const v = tile.indices[t];
    for (let c = 0; c < 3; c++) {
      const x = p[v * 3 + c];
      if (x < h.bboxMin[c] - eps || x > h.bboxMax[c] + eps) {
        errs.push('bbox does not contain all vertices');
        t = tile.indices.length;
        break;
      }
    }
  }
  let needFix = false;
  for (const m of tile.materials) if (materialHasFlag(m, MAT_FLAG.FIX_EDGES)) { needFix = true; break; }
  if (needFix && !(h.flags & COL_FLAG.FIX_INTERNAL_EDGES)) errs.push('FIX_EDGES material present but FIX_INTERNAL_EDGES flag not set');
  let degenerate = 0;
  for (let t = 0; t < h.triangleCount; t++) {
    const a = tile.indices[t * 3], b = tile.indices[t * 3 + 1], c = tile.indices[t * 3 + 2];
    const ux = p[b * 3] - p[a * 3], uy = p[b * 3 + 1] - p[a * 3 + 1], uz = p[b * 3 + 2] - p[a * 3 + 2];
    const vx = p[c * 3] - p[a * 3], vy = p[c * 3 + 1] - p[a * 3 + 1], vz = p[c * 3 + 2] - p[a * 3 + 2];
    const area = 0.5 * Math.hypot(uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx);
    if (area < 1e-6) degenerate++;
  }
  if (degenerate) errs.push(`${degenerate} degenerate triangles`);
  return errs;
}

interface Station {
  source: string;
  x: number;
  z: number;
  nx: number; // horizontal unit normal pointing to the LOW side
  nz: number;
  low: number;
  high: number;
  step: number;
}

class Ctx {
  RAPIER: any;
  world: any;
  loaded: Loaded[];
  o: Options;
  rng: () => number;
  walker: Walker;
  tileKeys = new Set<string>();
  trimeshHandles = new Set<number>();
  minY = Infinity;
  maxY = -Infinity;
  aoi: [number, number, number, number];
  // trajectory below-ground bookkeeping (filled by every walker simulation)
  trajSamples = 0;
  belowSamples: any[] = [];
  belowCount = 0;

  constructor(RAPIER: any, world: any, loaded: Loaded[], o: Options) {
    this.RAPIER = RAPIER;
    this.world = world;
    this.loaded = loaded;
    this.o = o;
    this.rng = mulberry32(o.seed);
    this.walker = new Walker(RAPIER, world, o.kcc);
    let x0 = Infinity, z0 = Infinity, x1 = -Infinity, z1 = -Infinity;
    for (const l of loaded) {
      const h = l.tile.header;
      this.tileKeys.add(`${h.tileI},${h.tileJ}`);
      x0 = Math.min(x0, h.tileI * TILE); z0 = Math.min(z0, h.tileJ * TILE);
      x1 = Math.max(x1, (h.tileI + 1) * TILE); z1 = Math.max(z1, (h.tileJ + 1) * TILE);
      this.minY = Math.min(this.minY, h.origin[1] + h.bboxMin[1]);
      this.maxY = Math.max(this.maxY, h.origin[1] + h.bboxMax[1]);
      for (const c of l.built.colliders) if (c.kind === 'trimesh' && c.cls === 'solid') this.trimeshHandles.add(c.collider.handle);
    }
    this.aoi = o.aoi ?? [x0, z0, x1, z1];
  }

  aoiDesc() {
    return { bbox: this.aoi, tiles: [...this.tileKeys].map((k) => `c_${k.replace(',', '_')}`), yRange: [r3(this.minY), r3(this.maxY)] };
  }

  /** Point is inside the AOI and over a loaded tile (shrunk by `margin`). */
  inside(x: number, z: number, margin = 0): boolean {
    const [x0, z0, x1, z1] = this.aoi;
    if (x < x0 + margin || x > x1 - margin || z < z0 + margin || z > z1 - margin) return false;
    for (const [dx, dz] of [[0, 0], [margin, 0], [-margin, 0], [0, margin], [0, -margin]])
      if (!this.tileKeys.has(`${Math.floor((x + dx) / TILE)},${Math.floor((z + dz) / TILE)}`)) return false;
    return true;
  }

  /** Height of the first solid surface below (x, yFrom, z); NaN if none. */
  surfaceY(x: number, z: number, yFrom = this.maxY + 20, trimeshOnly = false): number {
    const ray = new this.RAPIER.Ray({ x, y: yFrom, z }, { x: 0, y: -1, z: 0 });
    const pred = trimeshOnly ? (c: any) => this.trimeshHandles.has(c.handle) : undefined;
    const hit = this.world.castRay(ray, yFrom - this.minY + 50, true, this.RAPIER.QueryFilterFlags.EXCLUDE_SENSORS, SOLID_QUERY_FILTER, undefined, undefined, pred);
    return hit ? yFrom - hit.timeOfImpact : NaN;
  }

  /** True when there is a solid directly above (x, y, z) within `dist` (i.e. under a roof). */
  solidAbove(x: number, y: number, z: number, dist = 200): boolean {
    const ray = new this.RAPIER.Ray({ x, y, z }, { x: 0, y: 1, z: 0 });
    return !!this.world.castRay(ray, dist, false, this.RAPIER.QueryFilterFlags.EXCLUDE_SENSORS, SOLID_QUERY_FILTER, undefined, undefined, (c: any) => this.trimeshHandles.has(c.handle));
  }

  /** Below ground = no solid surface at or under the feet (fell through), or far below the tiles. */
  isBelowGround(x: number, feetY: number, z: number): boolean {
    if (feetY < this.minY - 2) return true;
    const s = this.surfaceY(x, z, feetY + 0.15);
    return !Number.isFinite(s);
  }

  capsuleFree(x: number, y: number, z: number): boolean {
    const k = this.o.kcc;
    const shape = new this.RAPIER.Capsule(k.halfHeight, k.radius);
    const hit = this.world.intersectionWithShape({ x, y, z }, { x: 0, y: 0, z: 0, w: 1 }, shape, this.RAPIER.QueryFilterFlags.EXCLUDE_SENSORS, SOLID_QUERY_FILTER);
    return !hit;
  }

  /** Run the walker; `vel(frame)` returns [vx, vz]. Records below-ground samples. Calls `each` every frame. */
  simulate(frames: number, vel: (f: number) => [number, number], each?: (f: number) => boolean | void, label = ''): void {
    const w = this.walker;
    for (let f = 0; f < frames; f++) {
      let [vx, vz] = vel(f);
      const nx = w.x + vx / 60, nz = w.z + vz / 60;
      if (!this.inside(nx, nz, 1.0)) { vx = 0; vz = 0; } // never walk off the loaded area
      w.step(vx, vz);
      if (f % 10 === 9 || f === frames - 1) {
        this.trajSamples++;
        if (this.isBelowGround(w.x, w.feetY, w.z)) {
          this.belowCount++;
          if (this.belowSamples.length < this.o.maxSamples) this.belowSamples.push({ label, frame: f, x: r3(w.x), feetY: r3(w.feetY), z: r3(w.z) });
          return; // once below, the rest of the trajectory is meaningless
        }
      }
      if (each && each(f) === true) return;
    }
  }

  // ------------------------------------------------------------------------------ holes
  checkHoles() {
    const [x0, z0, x1, z1] = this.aoi;
    const step = Math.sqrt(1 / this.o.rayDensity);
    let rays = 0, misses = 0;
    const samples: any[] = [];
    for (let z = z0 + step / 2; z < z1; z += step)
      for (let x = x0 + step / 2; x < x1; x += step) {
        const jx = x + (this.rng() - 0.5) * step * 0.9, jz = z + (this.rng() - 0.5) * step * 0.9;
        if (!this.inside(jx, jz)) continue;
        rays++;
        if (!Number.isFinite(this.surfaceY(jx, jz, this.maxY + 20, true))) {
          misses++;
          if (samples.length < this.o.maxSamples) samples.push([r3(jx), r3(jz)]);
        }
      }
    return { status: misses === 0 && rays > 0 ? 'pass' : 'fail', rays, raySpacing: r3(step), misses, missSamples: samples };
  }

  // ------------------------------------------------------------------------------ buildings
  checkBuildings(fps: Footprint[]) {
    const idx = new FootprintIndex(fps);
    const usable = fps.filter((f) => {
      const [x0, z0, x1, z1] = f.bbox;
      return this.inside((x0 + x1) / 2, (z0 + z1) / 2);
    });
    if (!usable.length) return { status: 'skipped', reason: 'no footprint inside the loaded tiles', footprints: fps.length };
    const k = this.o.kcc;
    const frames = Math.round(this.o.walkSeconds * 60);
    let insideAny = 0, insideEnd = 0, started = 0, rejected = 0;
    const samples: any[] = [];
    const perFp = new Map<string, number>();
    for (let n = 0; n < this.o.walkers; n++) {
      let s: any = null;
      for (let tries = 0; tries < 30 && !s; tries++) s = this.walkerStart(usable, idx);
      if (!s) { rejected++; continue; }
      started++;
      const w = this.walker;
      w.place(s.x, s.ground, s.z);
      let hitInside = false;
      const isInside = () => {
        const f = idx.containing(w.x, w.z, 0.05);
        return f && this.solidAbove(w.x, w.y, w.z) ? f : undefined;
      };
      this.simulate(frames, () => [s.vx, s.vz], () => {
        const f = isInside();
        if (f && !hitInside) {
          hitInside = true;
          insideAny++;
          perFp.set(f.id, (perFp.get(f.id) ?? 0) + 1);
          if (samples.length < this.o.maxSamples) samples.push({ walker: n, footprint: f.id, start: [r3(s.x), r3(s.z)], at: [r3(w.x), r3(w.feetY), r3(w.z)] });
        }
      }, `building-walker-${n}`);
      if (isInside()) insideEnd++;
    }
    return {
      status: insideAny === 0 && insideEnd === 0 && started > 0 ? 'pass' : 'fail',
      footprints: fps.length,
      walkers: started,
      rejectedStarts: rejected,
      seconds: this.o.walkSeconds,
      insideAnyFrame: insideAny,
      insideAtEnd: insideEnd,
      perFootprint: Object.fromEntries(perFp),
      samples,
      capsule: { radius: k.radius, halfHeight: k.halfHeight },
    };
  }

  walkerStart(fps: Footprint[], idx: FootprintIndex) {
    const f = fps[Math.floor(this.rng() * fps.length)];
    const ring = f.rings[0];
    const orient = ringOrientation(ring);
    // edge weighted by length
    const lens = ring.map((p, i) => Math.hypot(ring[(i + 1) % ring.length][0] - p[0], ring[(i + 1) % ring.length][1] - p[1]));
    let r = this.rng() * lens.reduce((a, b) => a + b, 0), e = 0;
    while (r > lens[e] && e < lens.length - 1) r -= lens[e++];
    const [ax, az] = ring[e], [bx, bz] = ring[(e + 1) % ring.length];
    const t = this.rng();
    const ex = (bx - ax) / lens[e], ez = (bz - az) / lens[e];
    // outward normal: for a CCW ring (positive area in x/z) outward is (ez, -ex)
    const nx = orient > 0 ? ez : -ez, nz = orient > 0 ? -ex : ex;
    const d = 1.0 + this.rng() * 7;
    const x = ax + (bx - ax) * t + nx * d, z = az + (bz - az) * t + nz * d;
    if (!this.inside(x, z, 1.5) || idx.containing(x, z, -0.5)) return null;
    const ground = this.surfaceY(x, z);
    if (!Number.isFinite(ground)) return null;
    const k = this.o.kcc;
    if (!this.capsuleFree(x, ground + k.halfHeight + k.radius + 0.05, z)) return null;
    // aim at a random interior point, with up to ±30° deviation so walkers also slide along walls
    let tx = 0, tz = 0;
    for (let i = 0; i < 20; i++) {
      tx = f.bbox[0] + this.rng() * (f.bbox[2] - f.bbox[0]);
      tz = f.bbox[1] + this.rng() * (f.bbox[3] - f.bbox[1]);
      if (idx.containing(tx, tz) === f) break;
    }
    const a = Math.atan2(tz - z, tx - x) + (this.rng() - 0.5) * (Math.PI / 3);
    const speed = 1.5 + this.rng() * 4.5;
    return { x, z, ground, vx: Math.cos(a) * speed, vz: Math.sin(a) * speed };
  }

  // ------------------------------------------------------------------------------ curbs
  curbStationsFromTiles(): Station[] {
    const mats = new Set<number>([MAT.curb, MAT.quay, MAT.wall]);
    const out: Station[] = [];
    const seen = new Set<string>();
    const sp = this.o.curbSpacing;
    for (const { tile } of this.loaded) {
      const [ox, oy, oz] = tile.header.origin;
      const P = tile.positions, I = tile.indices;
      for (let t = 0; t < tile.header.triangleCount; t++) {
        const m = tile.materials[t];
        if (!mats.has(m)) continue;
        const v = [0, 1, 2].map((c) => {
          const i = I[t * 3 + c];
          return [ox + P[i * 3], oy + P[i * 3 + 1], oz + P[i * 3 + 2]];
        });
        const ux = v[1][0] - v[0][0], uy = v[1][1] - v[0][1], uz = v[1][2] - v[0][2];
        const wx = v[2][0] - v[0][0], wy = v[2][1] - v[0][1], wz = v[2][2] - v[0][2];
        let nx = uy * wz - uz * wy, ny = uz * wx - ux * wz, nz = ux * wy - uy * wx;
        const L = Math.hypot(nx, ny, nz);
        if (L === 0) continue;
        nx /= L; ny /= L; nz /= L;
        if (Math.abs(ny) > 0.5) continue; // not a vertical-ish face
        const hl = Math.hypot(nx, nz);
        const hx = nx / hl, hz = nz / hl; // outward = low side
        const tx = -hz, tz = hx; // along the face
        const s = v.map((p) => p[0] * tx + p[2] * tz);
        const smin = Math.min(...s), smax = Math.max(...s);
        if (smax - smin < 0.6) continue;
        const cx = (v[0][0] + v[1][0] + v[2][0]) / 3, cz = (v[0][2] + v[1][2] + v[2][2]) / 3;
        const dc = cx * hx + cz * hz; // face line offset
        const first = Math.ceil((smin + 0.5) / sp) * sp;
        for (let q = first; q <= smax - 0.5; q += sp) {
          const x = q * tx + dc * hx, z = q * tz + dc * hz;
          const key = `${Math.round(x / (sp / 2))},${Math.round(z / (sp / 2))},${Math.round(Math.atan2(hz, hx) * 8 / Math.PI)}`;
          if (seen.has(key)) continue;
          seen.add(key);
          out.push(this.measureStation(`${tile.id}:${MATERIALS[m].name}`, x, z, hx, hz));
        }
      }
    }
    return out;
  }

  curbStationsFromLines(fc: any): Station[] {
    const out: Station[] = [];
    const sp = this.o.curbSpacing;
    for (const line of loadLines(fc)) {
      const pts = line.points;
      for (let i = 0; i + 1 < pts.length; i++) {
        const [ax, az] = pts[i], [bx, bz] = pts[i + 1];
        const L = Math.hypot(bx - ax, bz - az);
        if (L < 0.6) continue;
        const ex = (bx - ax) / L, ez = (bz - az) / L;
        for (let d = Math.min(0.5, L / 2); d <= L - 0.5 + 1e-9; d += sp) {
          const x = ax + ex * d, z = az + ez * d;
          let nx = ez, nz = -ex;
          const a = this.surfaceY(x + nx * 0.6, z + nz * 0.6), b = this.surfaceY(x - nx * 0.6, z - nz * 0.6);
          if (a > b) { nx = -nx; nz = -nz; }
          out.push(this.measureStation(line.id, x, z, nx, nz));
        }
      }
    }
    return out;
  }

  measureStation(source: string, x: number, z: number, nx: number, nz: number): Station {
    const low = this.surfaceY(x + nx * 0.6, z + nz * 0.6);
    let high = -Infinity;
    for (const d of [0.05, 0.1, 0.2, 0.3, 0.45]) {
      const h = this.surfaceY(x - nx * d, z - nz * d);
      if (Number.isFinite(h)) high = Math.max(high, h);
    }
    return { source, x, z, nx, nz, low, high, step: high - low };
  }

  checkCurbs(lines?: any) {
    const stations = (lines ? this.curbStationsFromLines(lines) : this.curbStationsFromTiles()).filter((s) => this.inside(s.x, s.z, 1.5));
    const k = this.o.kcc;
    let climbExpected = 0, climbed = 0, blockExpected = 0, blocked = 0, info = 0, flush = 0, invalid = 0;
    const failures: any[] = [];
    const results: any[] = [];
    for (const st of stations) {
      if (!Number.isFinite(st.step)) { invalid++; continue; }
      if (st.step < 0.03) { flush++; continue; }
      const expect = st.step <= this.o.climbRequired ? 'climb' : st.step >= this.o.blockRequired ? 'block' : 'info';
      const sx = st.x + st.nx * 1.0, sz = st.z + st.nz * 1.0;
      const per: any = { source: st.source, x: r3(st.x), z: r3(st.z), step: r3(st.step), expect, speeds: {} };
      let ok = true;
      for (const v of this.o.curbSpeeds) {
        const g = this.surfaceY(sx, sz);
        if (!Number.isFinite(g) || !this.capsuleFree(sx, g + k.halfHeight + k.radius + 0.05, sz)) { per.speeds[v] = 'no-start'; continue; }
        this.walker.place(sx, g, sz);
        const frames = Math.round(Math.max(2.5, 3.0 / v) * 60);
        let crossed = false;
        this.simulate(frames, () => [-st.nx * v, -st.nz * v], () => {
          const past = (st.x - this.walker.x) * st.nx + (st.z - this.walker.z) * st.nz;
          if (past >= 0.6 && this.walker.feetY >= st.high - 0.06) { crossed = true; return true; }
        }, `curb-${st.source}`);
        per.speeds[v] = crossed ? 'climbed' : 'blocked';
        if (expect === 'climb' && !crossed) ok = false;
        if (expect === 'block' && crossed) ok = false;
      }
      const outcomes = Object.values(per.speeds).filter((s) => s !== 'no-start');
      if (!outcomes.length) { invalid++; continue; }
      per.ok = expect === 'info' ? null : ok;
      if (expect === 'climb') { climbExpected++; if (ok) climbed++; }
      else if (expect === 'block') { blockExpected++; if (ok) blocked++; }
      else info++;
      if (per.ok === false && failures.length < this.o.maxSamples) failures.push(per);
      results.push(per);
    }
    const tested = climbExpected + blockExpected + info;
    const bySource: Record<string, any> = {};
    for (const r of results) {
      const key = r.source;
      const b = (bySource[key] ??= { stations: 0, steps: [] as number[], outcomes: {} as Record<string, number> });
      b.stations++;
      b.steps.push(r.step);
      for (const o of Object.values(r.speeds) as string[]) b.outcomes[o] = (b.outcomes[o] ?? 0) + 1;
    }
    for (const b of Object.values(bySource)) {
      const s = (b.steps as number[]).sort((x, y) => x - y);
      b.stepMedian = s[s.length >> 1];
      delete b.steps;
    }
    const byStep: Record<string, any> = {};
    for (const r of results) {
      const key = (Math.round(r.step * 100) / 100).toFixed(2);
      const b = (byStep[key] ??= { expect: r.expect, stations: 0, outcomes: {} as Record<string, number> });
      b.stations++;
      for (const o of Object.values(r.speeds) as string[]) b.outcomes[o] = (b.outcomes[o] ?? 0) + 1;
    }
    return {
      status: tested === 0 ? 'skipped' : climbed === climbExpected && blocked === blockExpected ? 'pass' : 'fail',
      mode: lines ? 'lines' : 'tiles',
      thresholds: { climbRequired: this.o.climbRequired, blockRequired: this.o.blockRequired, speeds: this.o.curbSpeeds },
      stations: stations.length,
      flush,
      invalid,
      climbExpected,
      climbed,
      blockExpected,
      blocked,
      infoBand: info,
      bySource,
      byStep,
      failures,
    };
  }

  // ------------------------------------------------------------------------------ stairs
  checkStairs() {
    const flights: any[] = [];
    for (const { tile } of this.loaded) {
      const [ox, oy, oz] = tile.header.origin;
      const P = tile.positions, I = tile.indices;
      const tris: number[] = [];
      for (let t = 0; t < tile.header.triangleCount; t++) if (tile.materials[t] === MAT.stair_slope) tris.push(t);
      if (!tris.length) continue;
      // connected components by shared vertex
      const parent = new Map<number, number>();
      const find = (a: number): number => { while (parent.get(a)! !== a) { parent.set(a, parent.get(parent.get(a)!)!); a = parent.get(a)!; } return a; };
      for (const t of tris) for (let c = 0; c < 3; c++) if (!parent.has(I[t * 3 + c])) parent.set(I[t * 3 + c], I[t * 3 + c]);
      for (const t of tris) { const a = find(I[t * 3]); parent.set(find(I[t * 3 + 1]), a); parent.set(find(I[t * 3 + 2]), a); }
      const comps = new Map<number, number[]>();
      for (const t of tris) { const r = find(I[t * 3]); if (!comps.has(r)) comps.set(r, []); comps.get(r)!.push(t); }
      for (const ts of comps.values()) flights.push(this.testFlight(`${tile.id}#${flights.length}`, ts.map((t) => [0, 1, 2].map((c) => {
        const i = I[t * 3 + c];
        return [ox + P[i * 3], oy + P[i * 3 + 1], oz + P[i * 3 + 2]];
      }))));
    }
    if (!flights.length) return { status: 'skipped', reason: 'no stair_slope triangles', flights: [] };
    const bad = flights.filter((f) => !f.ok);
    return { status: bad.length ? 'fail' : 'pass', count: flights.length, failed: bad.length, flights };
  }

  testFlight(id: string, tris: number[][][]) {
    let Nx = 0, Ny = 0, Nz = 0;
    for (const [a, b, c] of tris) {
      const ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2], vx = c[0] - a[0], vy = c[1] - a[1], vz = c[2] - a[2];
      Nx += uy * vz - uz * vy; Ny += uz * vx - ux * vz; Nz += ux * vy - uy * vx;
    }
    if (Ny < 0) { Nx = -Nx; Ny = -Ny; Nz = -Nz; }
    const L = Math.hypot(Nx, Ny, Nz);
    const slope = (Math.acos(Ny / L) * 180) / Math.PI;
    const hl = Math.hypot(Nx, Nz);
    const ux = -Nx / hl, uz = -Nz / hl; // uphill
    const wx = -uz, wz = ux;
    let smin = Infinity, smax = -Infinity, lmin = Infinity, lmax = -Infinity, ybot = Infinity, ytop = -Infinity;
    for (const t of tris) for (const p of t) {
      const s = p[0] * ux + p[2] * uz, l = p[0] * wx + p[2] * wz;
      smin = Math.min(smin, s); smax = Math.max(smax, s); lmin = Math.min(lmin, l); lmax = Math.max(lmax, l);
      ybot = Math.min(ybot, p[1]); ytop = Math.max(ytop, p[1]);
    }
    const lc = (lmin + lmax) / 2;
    const at = (s: number) => [s * ux + lc * wx, s * uz + lc * wz];
    const k = this.o.kcc;
    const speed = 1.5;
    const frames = Math.round(((smax - smin + 2.4) / speed) * 60 * 1.5 + 60);
    const run = (dir: 1 | -1) => {
      const [sx, sz] = at(dir > 0 ? smin - 1.2 : smax + 1.2);
      const g = this.surfaceY(sx, sz, ytop + 3);
      if (!Number.isFinite(g) || !this.capsuleFree(sx, g + k.halfHeight + k.radius + 0.05, sz)) return { result: 'no-start' };
      this.walker.place(sx, g, sz);
      let done = false;
      this.simulate(frames, () => [ux * speed * dir, uz * speed * dir], () => {
        const s = this.walker.x * ux + this.walker.z * uz;
        if (dir > 0 && s >= smax + 0.3 && Math.abs(this.walker.feetY - ytop) <= 0.1) { done = true; return true; }
        if (dir < 0 && s <= smin - 0.3 && Math.abs(this.walker.feetY - ybot) <= 0.1) { done = true; return true; }
      }, `stair-${id}`);
      const w = this.walker;
      return { result: done ? 'ok' : 'stuck', end: [r3(w.x), r3(w.feetY), r3(w.z)] };
    };
    const up = run(1), down = run(-1);
    const slopeFlag = slope > this.o.stairMaxSlopeDeg;
    return {
      id,
      triangles: tris.length,
      slopeDeg: r3(slope),
      slopeFlag,
      rise: r3(ytop - ybot),
      run: r3(smax - smin),
      width: r3(lmax - lmin),
      bottom: at(smin).map(r3),
      top: at(smax).map(r3),
      up,
      down,
      ok: up.result === 'ok' && down.result === 'ok',
    };
  }

  // ------------------------------------------------------------------------------ below ground
  checkBelowGround() {
    const [x0, z0, x1, z1] = this.aoi;
    const sp = this.o.dropSpacing;
    let drops = 0, voids = 0;
    const voidSamples: any[] = [];
    const before = this.belowCount;
    for (let z = z0 + sp / 2; z < z1; z += sp)
      for (let x = x0 + sp / 2; x < x1; x += sp) {
        const jx = x + (this.rng() - 0.5) * sp * 0.8, jz = z + (this.rng() - 0.5) * sp * 0.8;
        if (!this.inside(jx, jz, 1.0)) continue;
        drops++;
        const top = this.surfaceY(jx, jz);
        const start = Number.isFinite(top) ? top + 1.0 : this.maxY + 1;
        if (!Number.isFinite(top)) { voids++; if (voidSamples.length < this.o.maxSamples) voidSamples.push([r3(jx), r3(jz)]); }
        this.walker.place(jx, start, jz);
        this.simulate(120, () => [0, 0], undefined, `drop-${r3(jx)}-${r3(jz)}`);
      }
    const dropBelow = this.belowCount - before;
    return {
      status: this.belowCount === 0 && voids === 0 ? 'pass' : 'fail',
      drops,
      dropsWithoutSurface: voids,
      voidSamples,
      dropsBelowGround: dropBelow,
      trajectorySamples: this.trajSamples,
      belowGroundSamples: this.belowCount,
      samples: this.belowSamples,
      note: 'trajectory samples cover every walker run (buildings, curbs, stairs, drops) made before this check',
    };
  }
}
