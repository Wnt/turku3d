// Drive replay of Jonni's capture 20261104-210653-1hp6in ("the car twitches back and forth while the camera keeps
// moving"): the recorded frame times and the car's recorded path at its recorded speeds, delivered by a model of the
// physics worker (60 Hz timer with lateness, the capture's per-snapshot step times and its phys.stall events at their
// worker times, the 5-substep cap). Each frame draws the driven car and aims the chase camera; the pose discontinuity
// metric is the per-frame acceleration of the drawn car in the world and relative to the camera target.
//   CARJITTER_CAPTURE=~/turku3d/captures/20261104-210653-1hp6in npx vitest run -c vitest.perf.config.ts tests/perf/carjitter.test.ts
// CARJITTER_OLD=/path/to/old/interpolation.ts adds that VehicleInterpolation (old push(current, previous, tick,
// remainder, now) signature) as a third pipeline.
import { expect, test } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { VehicleInterpolation } from '../../src/actors/interpolation';
import type { VehicleState } from '../../src/physics/protocol';

const dir = process.env.CARJITTER_CAPTURE;
const DT = 1 / 60, MAX_SUBSTEPS = 5;
type V = [number, number, number];
interface Pose { t: number; pos: V; vel: V; yaw: number }

function load() {
  const read = (f: string) => fs.readFileSync(path.join(dir!, f), 'utf8').trim().split('\n').map(l => JSON.parse(l));
  const marks = read('marks.jsonl') as { k: string; t: number }[];
  const resume = marks.find(m => m.k === 'resume')!.t, pause = marks.find(m => m.k === 'pause' && m.t > resume)?.t ?? Infinity;
  const raw = (read('poses.jsonl') as any[]).filter(p => !p.paused && p.car && p.t > resume && p.t < pause).sort((a, b) => a.t - b.t);
  // Ground truth: the recorded velocities integrated (the recorded positions are themselves raw, step-quantised
  // states); heading from the recorded chassis rotation, unwrapped.
  const route: Pose[] = [];
  let yawPrev = 0;
  for (const p of raw) {
    const [, , , qx, qy, qz, qw, vx, vy, vz] = p.car;
    let yaw = Math.atan2(2 * (qw * qy + qx * qz), 1 - 2 * (qy * qy + qz * qz));
    if (route.length) { while (yaw - yawPrev > Math.PI) yaw -= 2 * Math.PI; while (yaw - yawPrev < -Math.PI) yaw += 2 * Math.PI; }
    yawPrev = yaw;
    const last = route.at(-1);
    const pos: V = last ? [0, 1, 2].map(i => last.pos[i] + (last.vel[i] + [vx, vy, vz][i]) / 2 * (p.t - last.t) / 1000) as V : [p.car[0], p.car[1], p.car[2]];
    route.push({ t: p.t, pos, vel: [vx, vy, vz], yaw });
  }
  const steps = raw.map(p => p.phys.stepMs as number).sort((a, b) => a - b);
  const stalls = (read('tiles.jsonl') as any[]).filter(e => e.k === 'phys.stall' && (e.cause === 'step' || e.cause === 'gap'))
    .map(e => ({ at: e.t - e.ago, ms: e.ms as number })).filter(s => s.at > resume && s.at < pause);
  const frames = (read('frames.jsonl') as number[][][]).flat().filter(f => f[0] > raw[0].t && f[0] < raw.at(-1)!.t)
    .map(f => ({ t: f[0], cpu: Math.max(1, f[2]) }));
  return { path: route, steps, stalls, frames };
}

/** The car's true state at capture time t (ms). */
function truth(path: Pose[], t: number): Pose {
  let i = 1;
  while (i < path.length - 1 && path[i].t < t) i++;
  const a = path[i - 1], b = path[i], f = Math.max(0, Math.min(1, (t - a.t) / (b.t - a.t)));
  const vel = [0, 1, 2].map(k => a.vel[k] + (b.vel[k] - a.vel[k]) * f) as V;
  const s = (t - a.t) / 1000;
  const pos = [0, 1, 2].map(k => a.pos[k] + (a.vel[k] + vel[k]) / 2 * s) as V;
  return { t, pos, vel, yaw: a.yaw + (b.yaw - a.yaw) * f };
}

function state(p: Pose): VehicleState {
  return { id: 'tr:car', pos: { x: p.pos[0], y: p.pos[1], z: p.pos[2] }, rot: { x: 0, y: Math.sin(p.yaw / 2), z: 0, w: Math.cos(p.yaw / 2) },
    linvel: { x: p.vel[0], y: p.vel[1], z: p.vel[2] }, speed: Math.hypot(...p.vel), forwardSpeed: Math.hypot(...p.vel), curbStrike: null, wheels: [] };
}

interface Published { at: number; tick: number; remainder: number; current: VehicleState; previous: VehicleState }
/** The worker: a 16.7 ms timer that fires late by 0-4 ms (more under load), fixed steps of the wall time since the
 *  last wake (at most five), a step cost drawn from the capture's snapshot step times or the recorded stall at that
 *  moment, and the state published when the step ends. */
function worker(data: ReturnType<typeof load>, seed = 11) {
  let s = seed;
  const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
  const { path, steps, stalls } = data;
  const start = path[0].t, end = path.at(-1)!.t;
  const out: Published[] = [];
  let wake = start, last = start, acc = 0, tick = 0, stall = 0;
  const physicsAt = (k: number) => start + k * DT * 1000;
  while (wake < end) {
    let cost = steps[Math.floor(rnd() * steps.length * 0.97)];
    while (stall < stalls.length && stalls[stall].at <= wake + 16.7) cost = Math.max(cost, stalls[stall++].ms);
    acc = Math.min(MAX_SUBSTEPS * DT, acc + (wake - last) / 1000);
    last = wake;
    const n = Math.floor(acc / DT + 1e-9);
    acc -= n * DT;
    if (n > 0) {
      tick += n;
      out.push({ at: wake + cost, tick, remainder: acc, current: state(truth(path, physicsAt(tick))), previous: state(truth(path, physicsAt(tick - 1))) });
    }
    wake = Math.max(wake + 16.7, wake + cost) + Math.pow(rnd(), 3) * 4;
  }
  return { out, physicsAt };
}

interface Drawn { t: number; car: V; target: V }
type Pipeline = (data: ReturnType<typeof load>) => Drawn[];
const v3 = (s: VehicleState): V => [s.pos.x, s.pos.y, s.pos.z];

/** As driven in the capture (a taken traffic car): the car drawn at the raw state read at frame start, the chase
 *  camera aimed at the raw state re-read later in the same frame. */
const rawPipeline: Pipeline = data => {
  const { out } = worker(data);
  const newest = (t: number) => { let k = -1; while (k + 1 < out.length && out[k + 1].at <= t) k++; return k >= 0 ? out[k].current : null; };
  return data.frames.map(f => {
    const begin = f.t - f.cpu, car = newest(begin + 0.3), cam = newest(begin + 0.5 * f.cpu);
    return car && cam ? { t: begin, car: v3(car), target: v3(cam) } : null;
  }).filter((d): d is Drawn => !!d);
};

/** One render pose per frame from an interpolation class; the camera follows it. */
const interpolated = (make: () => { push: (...a: any[]) => void; sample: (now: number, e?: boolean) => VehicleState | null }, oldSignature: boolean): Pipeline => data => {
  const { out } = worker(data);
  const render = make();
  let k = 0;
  const drawn: Drawn[] = [];
  for (const f of data.frames) {
    const begin = f.t - f.cpu;
    // The shared mailbox holds only the newest state: intermediate snapshots are never seen.
    let m: Published | null = null;
    while (k < out.length && out[k].at <= begin + 0.3) m = out[k++];
    if (m) oldSignature ? render.push(m.current, m.previous, m.tick, m.remainder, m.at / 1000) : render.push(m.current, m.previous, m.tick, m.at / 1000);
    const s = render.sample(begin / 1000, true);
    if (s) drawn.push({ t: begin, car: v3(s), target: v3(s) });
  }
  return drawn;
};

const pct = (a: number[], q: number) => { const s = a.toSorted((x, y) => x - y); return s[Math.min(s.length - 1, Math.floor(q * s.length))] ?? 0; };
/** Pose discontinuity: per-frame acceleration (m/s²) of the drawn car in the world and relative to the camera target. */
function metric(drawn: Drawn[], data: ReturnType<typeof load>) {
  const world: number[] = [], relative: number[] = [], lock: number[] = [], lag: number[] = [];
  const accel = (p: (d: Drawn) => V, k: number) => {
    const a = drawn[k - 1], b = drawn[k], c = drawn[k + 1], d1 = (b.t - a.t) / 1000, d2 = (c.t - b.t) / 1000;
    const pa = p(a), pb = p(b), pc = p(c);
    return Math.hypot(...[0, 1, 2].map(i => ((pc[i] - pb[i]) / d2 - (pb[i] - pa[i]) / d1) / ((d1 + d2) / 2)) as V);
  };
  for (let k = 1; k < drawn.length - 1; k++) {
    world.push(accel(d => d.car, k));
    relative.push(accel(d => [d.car[0] - d.target[0], d.car[1] - d.target[1], d.car[2] - d.target[2]], k));
  }
  for (const d of drawn) {
    lock.push(Math.hypot(d.car[0] - d.target[0], d.car[1] - d.target[1], d.car[2] - d.target[2]));
    const g = truth(data.path, d.t);
    lag.push(Math.hypot(...[0, 1, 2].map(i => d.car[i] - g.pos[i]) as V));
  }
  const truthAccel = metric0(data);
  const r = (x: number) => +x.toFixed(2);
  return { frames: drawn.length, worldP50: r(pct(world, 0.5)), worldP99: r(pct(world, 0.99)), worldMax: r(Math.max(...world)),
    worldSpikes: world.filter(a => a > Math.max(50, 3 * truthAccel)).length,
    relativeP99: r(pct(relative, 0.99)), relativeMax: r(Math.max(...relative)), relativeSpikes: relative.filter(a => a > 50).length,
    lockP99: r(pct(lock, 0.99)), lockMax: r(Math.max(...lock)), offTruthP50: r(pct(lag, 0.5)), offTruthP99: r(pct(lag, 0.99)) };
}
/** The true path's own p99 acceleration at the frame times (what a perfect render would show). */
function metric0(data: ReturnType<typeof load>) {
  const a: number[] = [], f = data.frames.map(x => x.t - x.cpu);
  for (let k = 1; k < f.length - 1; k++) {
    const p = [f[k - 1], f[k], f[k + 1]].map(t => truth(data.path, t).pos), d1 = (f[k] - f[k - 1]) / 1000, d2 = (f[k + 1] - f[k]) / 1000;
    a.push(Math.hypot(...[0, 1, 2].map(i => ((p[2][i] - p[1][i]) / d2 - (p[1][i] - p[0][i]) / d1) / ((d1 + d2) / 2)) as V));
  }
  return pct(a, 0.99);
}

test.skipIf(!dir)('drive replay of capture 20261104-210653-1hp6in: the drawn car has no pose discontinuities', async () => {
  const data = load();
  const result: Record<string, ReturnType<typeof metric>> = {};
  result.raw = metric(rawPipeline(data), data);
  if (process.env.CARJITTER_OLD) {
    const old = await import(process.env.CARJITTER_OLD);
    result.oldInterpolation = metric(interpolated(() => new old.VehicleInterpolation(), true)(data), data);
  }
  result.now = metric(interpolated(() => new VehicleInterpolation(), false)(data), data);
  console.log(JSON.stringify({ truthAccelP99: +metric0(data).toFixed(2), stalls: data.stalls.length, ...result }, null, 1));
  // The car is drawn where the camera aims, and its acceleration stays at the true path's level.
  expect(result.now.lockMax).toBe(0);
  expect(result.now.worldSpikes).toBeLessThanOrEqual(Math.ceil(result.now.frames * 0.002));
  expect(result.now.worldP99).toBeLessThan(result.raw.worldP99 / 5);
}, 120000);
