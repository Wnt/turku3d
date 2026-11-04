// The bridge, tunnel and underpass drive sweep (tools/drivetest/README.md). Each lane on each structure is driven by
// the autopilot's drive follower (gameplay/autopilot/follow.ts) on the real vehicle physics (physics/world.ts, the
// worker's own code) over the pinned tile version's collision cells, at the lane's posted limit, and traced
// (qa/drivetest/trace.ts). Results append to DIR/results/*.jsonl, one row per lane; a rerun skips finished lanes.
import fs from 'node:fs';
import path from 'node:path';
import * as RAPIER from '@dimforge/rapier3d';
import { PhysicsWorld } from '../../src/physics/world';
import { DT } from '../../src/physics/constants';
import { MATERIALS } from '../../src/shared/col/materials';
import { COLLISION_TILE, engineToEN } from '../../src/core/origin';
import { DriveFollower } from '../../src/gameplay/autopilot/follow';
import { LaneRouter, tilesBetween } from '../../src/gameplay/autopilot/lanes';
import { sampleAt, type Path } from '../../src/gameplay/autopilot/path';
import { vehicleGeometry, vehiclePose } from '../../src/gameplay/autopilot/autopilot';
import { findStructures, lanesOn, type OsmWay, type Structure } from '../../src/qa/drivetest/structures';
import { DriveTrace } from '../../src/qa/drivetest/trace';
// @ts-expect-error plain ESM tool
import { TileCache, farm } from '../../../tools/drivetest/fetch.mjs';

export interface SweepOptions { dir: string; shard: [number, number]; limit?: number; only?: string[]; log?: (s: string) => void }

/** Margin (m) before and after the structure that still counts: its joints and approach slabs. */
const JOINT_MARGIN = 15;

export async function structuresOf(dir: string): Promise<Structure[]> {
  const file = path.join(dir, 'structures.json');
  if (fs.existsSync(file)) return JSON.parse(fs.readFileSync(file, 'utf8'));
  const cache = new TileCache(dir, farm(path.join(dir, 'tile-service.json')));
  const tiles: string[] = await cache.city();
  const roads = (await Promise.all(tiles.map(t => cache.roads(t)))).filter(Boolean);
  const osmFile = path.join(dir, 'osm-structures.json');
  const osm: OsmWay[] = fs.existsSync(osmFile) ? JSON.parse(fs.readFileSync(osmFile, 'utf8')).ways : [];
  const found = findStructures(roads, osm);
  fs.writeFileSync(file, JSON.stringify(found));
  return found;
}

function cellsAlong(p: Path): string[] {
  const out = new Set<string>();
  for (let s = 0; s <= p.length; s += 10) {
    const at = sampleAt(p, s), i = Math.floor(at.x / COLLISION_TILE), j = Math.floor(at.z / COLLISION_TILE);
    for (let a = -1; a <= 1; a++) for (let b = -1; b <= 1; b++) out.add(`c_${i + a}_${j + b}`);
  }
  return [...out];
}

/** Ground steps under both wheel tracks: collision rays every 10 cm through the window. */
/** Carriageway materials: a wheel track on anything else means the lane runs over a kerb, a sidewalk or verge. */
const CARRIAGEWAY = new Set(['road', 'crossing', 'bridge_deck', 'cobblestone', 'paving_slab', 'ramp', 'unknown']);
/** `above(s)`: where to start each ray, about 1.7 m over the driven surface (the car's chassis height there), so a
 *  lane whose surveyed height misses the deck never rays from under it. */
function groundSteps(pw: PhysicsWorld, p: Path, from: number, to: number, above: (s: number) => number | null, half = 0.8) {
  const steps: { s: number; x: number; y: number; z: number; height: number; track: string }[] = [];
  const surfaces: Record<string, number> = {};
  for (const [track, side] of [['left', -half], ['right', half]] as const) {
    const ys: (number | null)[] = [], at: { s: number; x: number; z: number }[] = [];
    for (let s = Math.max(0, from); s <= Math.min(p.length, to); s += 0.1) {
      const q = sampleAt(p, s), x = q.x - q.hz * side, z = q.z + q.hx * side;
      const base = above(s);
      if (base === null) continue;
      const top = base + 1.2;
      const hit = pw.raycast({ x, y: top, z }, { x: 0, y: -1, z: 0 }, 4, 'solid');
      ys.push(hit ? top - hit.toi : null); at.push({ s, x, z });
      const name = hit ? MATERIALS[hit.material]?.name ?? 'unknown' : 'none';
      surfaces[name] = (surfaces[name] ?? 0) + 1;
    }
    let last = -Infinity;
    for (let k = 5; k < ys.length - 7; k++) {
      const y0 = ys[k], y2 = ys[k + 2];
      if (y0 === null || y2 === null) {
        if (at[k].s - last > 1 && ys.slice(k, k + 3).every(v => v === null)) { steps.push({ ...at[k], y: NaN, height: 1, track: `${track} (no ground)` }); last = at[k].s; }
        continue;
      }
      // The slope on either side (50 cm before and after) is the road's own; a step is the rise beyond it.
      const before = ys[k - 5], after = ys[k + 7];
      if (before === null || after === null) continue;
      const slope = ((y0 - before) / 0.5 + (after - y2) / 0.5) / 2;
      const height = Math.abs(y2 - y0 - slope * 0.2);
      // A jump of more than 30 cm is a hole, a gap between deck pieces or an object standing on the lane.
      if (height >= 0.02 && at[k].s - last > 1) { steps.push({ ...at[k], y: y0, height, track }); last = at[k].s; }
      else if (height >= 0.02 && steps.length && height > steps.at(-1)!.height && at[k].s - last <= 1) { Object.assign(steps.at(-1)!, { ...at[k], y: y0, height }); last = at[k].s; }
    }
  }
  const total = Object.values(surfaces).reduce((a, b) => a + b, 0) || 1;
  const off = Object.entries(surfaces).filter(([k]) => !CARRIAGEWAY.has(k)).reduce((a, [, n]) => a + n, 0) / total;
  return { steps, surfaces, offRoad: off };
}

export interface LaneResult {
  key: string; structure: string; kind: Structure['kind']; name: string; way: number; highway: string; over?: Structure['over'];
  lane: string; k: number; laneCount: number; heading: number; limitKmh: number;
  center: Structure['center']; severity: string; peaks: Record<string, number>; worst: unknown; events: unknown[];
  driven: number; window: [number, number]; surfaces: Record<string, number>; offRoad: number; laneHeightOff: number; seconds: number; recoveries: number; outcome: string; wallMs: number;
  /** The worst point as a pose for a screenshot: engine x,y,z and heading, ETRS-TM35FIN. */
  view: { x: number; y: number; z: number; hx: number; hz: number; E: number; N: number } | null;
}

export async function sweep(o: SweepOptions): Promise<{ done: number; skipped: number; lanes: number }> {
  const log = o.log ?? console.log;
  const all = await structuresOf(o.dir);
  const mine = all.filter((s, i) => i % o.shard[1] === o.shard[0] && (!o.only || o.only.some(id => s.id === id || s.name === id)));
  const cache = new TileCache(o.dir, farm(path.join(o.dir, 'tile-service.json')));
  const resultsDir = path.join(o.dir, 'results'); fs.mkdirSync(resultsDir, { recursive: true });
  const out = path.join(resultsDir, `shard-${o.shard[0]}-of-${o.shard[1]}.jsonl`);
  const finished = new Set<string>();
  for (const f of fs.readdirSync(resultsDir)) for (const line of fs.readFileSync(path.join(resultsDir, f), 'utf8').split('\n')) if (line) finished.add(JSON.parse(line).key);
  let done = 0, skipped = 0, lanes = 0;
  for (const structure of mine) {
    if (o.limit !== undefined && done >= o.limit) break;
    const router = new LaneRouter();
    const box = tilesBetween(structure.center, structure.center, 1);
    for (const t of new Set([...box, ...structure.tiles])) { const r = await cache.roads(t); if (r) router.setTile(r); }
    const on = lanesOn(structure, router.lanes());
    lanes += on.length;
    if (!on.length) { log(`${structure.id} ${structure.name}: no drivable lane`); continue; }
    const todo = on.filter(l => !finished.has(`${structure.id}|${l.lane.tile}:${l.lane.id}`));
    if (!todo.length) { skipped += on.length; continue; }
    for (const { lane, from, to } of todo) {
      const key = `${structure.id}|${lane.tile}:${lane.id}`;
      const t0 = Date.now();
      const route = router.follow(lane, 90, 50);
      if (!route) continue;
      const window: [number, number] = [Math.max(0, route.laneStart + from - JOINT_MARGIN), Math.min(route.path.length, route.laneStart + to + JOINT_MARGIN)];
      const pw = new PhysicsWorld(RAPIER);
      for (const id of cellsAlong(route.path)) { const b = await cache.cell(id); if (b) await pw.loadTile(id, b); }
      pw.world.step();
      const trace = new DriveTrace(window);
      const start = sampleAt(route.path, 0);
      const ground = pw.raycast({ x: start.x, y: (start.y ?? 50) + 3, z: start.z }, { x: 0, y: -1, z: 0 }, 10, 'solid');
      const limit = route.limit(route.laneStart);
      let outcome = 'arrived', seconds = 0, recoveries = 0;
      if (!ground) { outcome = 'no ground at start'; trace.fail('incomplete', outcome, 0); }
      else {
        pw.spawnVehicle({ x: start.x, y: ground.point.y + 0.9, z: start.z }, Math.atan2(-start.hz, start.hx));
        for (let k = 0; k < 30; k++) pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
        const follower = new DriveFollower(route.path, vehicleGeometry('car'), { speed: Math.min(limit, 22), limit: route.limit, style: 'normal' });
        const maxSteps = Math.ceil((route.path.length / Math.max(4, limit * 0.4) + 30) / DT);
        let status = 'driving';
        for (let k = 0; k < maxSteps; k++) {
          const v = pw.vehicleState()!;
          const step = follower.step(vehiclePose(v), DT);
          status = step.status;
          const here = sampleAt(route.path, follower.s);
          trace.step(v, DT, follower.s, here.y);
          if (status === 'arrived' || status === 'off-path' || follower.recoveries > 2) break;
          pw.fixedStep(null, step.command);
          seconds += DT;
        }
        recoveries = follower.recoveries;
        if (status === 'off-path') { outcome = 'left the lane'; trace.fail('incomplete', outcome, follower.s); }
        else if (recoveries > 0) { outcome = `stuck (${recoveries} recoveries)`; trace.fail('stuck', outcome, follower.s); }
        else if (status !== 'arrived' && follower.s < window[1]) { outcome = 'timeout'; trace.fail('incomplete', outcome, follower.s); }
      }
      // Ground profile over the part driven, rayed from the car's own height; and how far the lane's surveyed
      // height is from the surface the car drove on.
      if (pw.vehicleState()) { pw.removeVehicle('player'); pw.world.step(); } // rays must not hit the car itself
      const driven = trace.samples.filter(p => p.s >= window[0] && p.s <= window[1]);
      const chassisAt = (s: number) => { let best: (typeof driven)[number] | null = null; for (const p of driven) if (!best || Math.abs(p.s - s) < Math.abs(best.s - s)) best = p; return best && Math.abs(best.s - s) < 2 ? best.y : null; };
      const ground0 = driven.length ? groundSteps(pw, route.path, window[0], Math.min(window[1], driven.at(-1)!.s), chassisAt) : { steps: [], surfaces: {}, offRoad: 0 };
      trace.addGround(ground0.steps);
      let laneHeightOff = 0;
      for (const p of driven) { const y = sampleAt(route.path, p.s).y; if (y !== null) laneHeightOff = Math.max(laneHeightOff, Math.abs(p.y - 0.65 - y)); }
      if (outcome !== 'arrived' && trace.events.some(e => (e.kind === 'stuck' || e.kind === 'incomplete') && e.severity === 'warn')) outcome = `not covered: ${outcome} before the structure`;
      pw.world.free();
      const summary = trace.summary();
      const worst = summary.worst as { s: number; x: number; y: number; z: number } | null;
      const view = worst ? (() => { const q = sampleAt(route.path, worst.s), en = engineToEN(worst.x, worst.y, worst.z);
        return { x: worst.x, y: worst.y, z: worst.z, hx: q.hx, hz: q.hz, E: Math.round(en.E * 10) / 10, N: Math.round(en.N * 10) / 10 }; })() : null;
      const h = sampleAt(route.path, route.laneStart);
      const row: LaneResult = { key, structure: structure.id, kind: structure.kind, name: structure.name, way: structure.way, highway: structure.highway,
        over: structure.over, lane: `${lane.tile}:${lane.id}`, k: lane.k, laneCount: lane.laneCount,
        heading: Math.round((Math.atan2(h.hx, -h.hz) * 180 / Math.PI + 360) % 360), limitKmh: Math.round(limit * 3.6),
        center: structure.center, severity: summary.severity, peaks: summary.peaks, worst: summary.worst,
        events: [...summary.events].sort((a, b) => (b.severity === 'fail' ? 1 : 0) - (a.severity === 'fail' ? 1 : 0)).slice(0, 40).map(e => ({ ...e, ...(() => { const en = engineToEN(e.x, e.y, e.z); return { E: Math.round(en.E * 10) / 10, N: Math.round(en.N * 10) / 10 }; })() })),
        driven: route.path.length, window, surfaces: ground0.surfaces, laneHeightOff: Math.round(laneHeightOff * 100) / 100, offRoad: Math.round(ground0.offRoad * 1000) / 1000, seconds: Math.round(seconds * 10) / 10, recoveries, outcome, wallMs: Date.now() - t0, view };
      fs.appendFileSync(out, JSON.stringify(row) + '\n');
      done++;
      log(`${structure.id} ${structure.name || ''} lane ${lane.k + 1}/${lane.laneCount} ${row.heading}°: ${summary.severity} ${outcome}` +
        ` (${summary.worst ? `${summary.worst.kind} ${summary.worst.value.toFixed(3)}` : '-'}) ${row.wallMs} ms`);
    }
  }
  return { done, skipped, lanes };
}
