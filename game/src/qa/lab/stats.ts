// Bench lab result math: frame distributions, heap/GC estimates, subsystem and per-tile stage splits. Pure functions,
// tested by game/tests/unit/bench-lab.test.ts.
import { SLICES } from '../../telemetry/streamTrace';

export interface Dist { n: number; mean: number | null; p50: number | null; p95: number | null; p99: number | null; max: number | null }

const r2 = (v: number) => Math.round(v * 100) / 100;

/** Nearest-rank percentiles (as render/performance.ts), mean and max, rounded to 0.01. */
export function dist(values: Iterable<number | null | undefined>): Dist {
  const sorted = [...values].filter((v): v is number => typeof v === 'number' && Number.isFinite(v)).sort((a, b) => a - b);
  const n = sorted.length;
  const at = (p: number) => n ? r2(sorted[Math.max(0, Math.ceil(n * p) - 1)]) : null;
  return { n, mean: n ? r2(sorted.reduce((a, b) => a + b, 0) / n) : null, p50: at(0.5), p95: at(0.95), p99: at(0.99),
    max: n ? r2(sorted[n - 1]) : null };
}

/** One rendered frame as the lab records it. */
export interface LabFrame {
  /** performance.now() at the frame's start (ms) and the interval since the previous frame's start. */
  t: number; interval: number | null;
  /** Renderer frame number (GPU readbacks are matched by it). */
  frame: number;
  /** Animation-loop callback time and the GPU time of the same frame, once its readback landed. */
  cpu: number; gpu: number | null;
  passes: Record<string, number> | null;
  draws: number; tris: number;
  /** usedJSHeapSize after the frame (bucketed unless Chrome runs with --enable-precise-memory-info). */
  heap: number | null;
  /** Per-subsystem ms inside the callback (SUBSYSTEMS order). */
  sub: number[];
  /** Streaming main-thread slices (telemetry/streamTrace.ts SLICES order), in or around this frame. */
  slices: number[];
  /** r186 node programs built inside this live frame (render/census.ts). */
  builds: number;
  /** Band/LOD trace events in this frame (fly). */
  bands: number;
}

export const SUBSYSTEMS = ['streaming', 'population', 'vegetation', 'props', 'lights', 'visibility', 'render'] as const;
export type Subsystem = typeof SUBSYSTEMS[number];

export interface HeapStats {
  start: number | null; end: number | null; peak: number | null;
  /** Sum of rises between samples: a lower bound on allocation (MB). */
  allocatedMB: number | null;
  /** Falls between samples: each is at least one collection. */
  drops: number;
  /** Whether samples change at sub-bucket granularity (precise memory info); otherwise these are not usable. */
  precise: boolean;
}

/** Allocation and collections from successive usedJSHeapSize samples. Chrome buckets the value and refreshes it
 *  rarely without --enable-precise-memory-info; `precise` says whether the samples moved like real heap sizes. */
export function heapStats(samples: (number | null)[]): HeapStats {
  const s = samples.filter((v): v is number => typeof v === 'number' && v > 0);
  if (!s.length) return { start: null, end: null, peak: null, allocatedMB: null, drops: 0, precise: false };
  let rise = 0, drops = 0, changes = 0, fine = 0;
  for (let i = 1; i < s.length; i++) {
    const d = s[i] - s[i - 1];
    if (d !== 0) { changes++; if (Math.abs(d) % 65536 !== 0) fine++; }
    if (d > 0) rise += d; else if (d < 0) drops++;
  }
  const mb = (v: number) => r2(v / 1048576);
  return { start: mb(s[0]), end: mb(s[s.length - 1]), peak: mb(Math.max(...s)), allocatedMB: mb(rise), drops,
    precise: changes >= Math.min(10, s.length - 1) && fine > 0 };
}

/** Frame-time distributions, long-frame counts and the frame rate over a measured window. */
export function frameSummary(frames: LabFrame[], elapsedMs?: number) {
  const intervals = frames.map(f => f.interval);
  const elapsed = elapsedMs ?? (frames.length > 1 ? frames[frames.length - 1].t - frames[0].t : 0);
  const longest = frames.reduce<LabFrame | null>((a, f) => (f.interval ?? 0) > (a?.interval ?? -1) ? f : a, null);
  const over = (ms: number) => frames.filter(f => (f.interval ?? 0) > ms).length;
  return {
    frames: frames.length, elapsedMs: Math.round(elapsed),
    fps: elapsed > 0 ? r2((frames.length - 1) * 1000 / elapsed) : null,
    cpuMs: dist(frames.map(f => f.cpu)), gpuMs: dist(frames.map(f => f.gpu)), intervalMs: dist(intervals),
    gpuFrames: frames.filter(f => f.gpu !== null).length,
    draws: dist(frames.map(f => f.draws)), tris: dist(frames.map(f => f.tris)),
    longFrames: { over33: over(33.4), over50: over(50), over100: over(100), over250: over(250) },
    longest: longest ? { t: Math.round(longest.t), intervalMs: r2(longest.interval ?? 0), cpuMs: r2(longest.cpu) } : null,
    inFrameBuilds: frames.reduce((a, f) => a + f.builds, 0),
  };
}

/** Per-pass GPU distributions over the frames whose pass timings landed (repeated passes already summed per frame). */
export function passSummary(frames: LabFrame[]) {
  const names = new Set<string>();
  for (const f of frames) if (f.passes) for (const k of Object.keys(f.passes)) names.add(k);
  const timed = frames.filter(f => f.passes);
  return { frames: timed.length,
    gpuMs: Object.fromEntries([...names].sort().map(k => [k, dist(timed.map(f => f.passes![k] ?? 0))])) };
}

/** Mean, p95 and max ms per frame for each subsystem, the unattributed rest of the callback, and streaming slices. */
export function subsystemSplit(frames: LabFrame[]) {
  const out: Record<string, { mean: number | null; p95: number | null; max: number | null; share: number | null }> = {};
  const total = frames.reduce((a, f) => a + f.cpu, 0);
  const add = (name: string, values: number[]) => {
    const d = dist(values), sum = values.reduce((a, b) => a + b, 0);
    out[name] = { mean: d.mean, p95: d.p95, max: d.max, share: total > 0 ? r2(sum / total) : null };
  };
  SUBSYSTEMS.forEach((name, i) => add(name, frames.map(f => f.sub[i] ?? 0)));
  add('other', frames.map(f => Math.max(0, f.cpu - f.sub.reduce((a, b) => a + b, 0))));
  const slices = Object.fromEntries(SLICES.map((name, i) => {
    const values = frames.map(f => f.slices[i] ?? 0), d = dist(values);
    return [name, { sum: r2(values.reduce((a, b) => a + b, 0)), p95: d.p95, max: d.max }];
  }));
  return { callback: out, slices };
}

/** A trace event as the lab keeps it: capture-clock time (ms), kind, tile/cell id, and its fields. */
export interface LabEvent { t: number; k: string; id?: string; ms?: number; [field: string]: unknown }

/** Streaming stages of one tile (telemetry/streamTrace.ts kinds), each logged when it ends with its duration. */
export const STAGES = ['tile.manifest', 'tile.collision', 'tile.glb', 'tile.buildings', 'tile.style', 'tile.facades',
  'tile.vegetation', 'tile.prepare', 'gfx.prepare', 'gfx.upload'] as const;

export interface StageWindow { stage: string; start: number; end: number }

/** The timed stage spans of `id` from its trace events. gfx.upload has no tile id; it is credited to the tile
 *  being installed when it ran. */
export function stageWindows(events: LabEvent[], id: string, from: number, to: number): StageWindow[] {
  return events.filter(e => (STAGES as readonly string[]).includes(e.k) && typeof e.ms === 'number'
    && (e.id === id || (e.k === 'gfx.upload' && e.id === undefined)) && e.t >= from && e.t - (e.ms as number) <= to)
    .map(e => ({ stage: e.k.replace(/^tile\./, ''), start: e.t - (e.ms as number), end: e.t }))
    .sort((a, b) => a.start - b.start);
}

/** The stage a frame starting at `t` falls in: the latest-started span still running, else `idle`. */
export function stageAt(windows: StageWindow[], t: number): string {
  let found = 'idle', start = -Infinity;
  for (const w of windows) if (w.start <= t && t <= w.end && w.start >= start) { found = w.stage; start = w.start; }
  return found;
}

/** Per-stage costs of one tile: wall time from the trace, and the frames rendered meanwhile (count, callback ms,
 *  the stall above the settled frame interval, the longest frame, streaming slices and in-frame program builds). */
export function stageSplit(frames: LabFrame[], windows: StageWindow[], baselineInterval: number) {
  const wall: Record<string, number> = {};
  for (const w of windows) wall[w.stage] = r2((wall[w.stage] ?? 0) + (w.end - w.start));
  const rows: Record<string, { wallMs: number | null; frames: number; cpuMs: number; stallMs: number; longestMs: number;
    sliceMs: number; builds: number }> = {};
  for (const f of frames) {
    const stage = stageAt(windows, f.t);
    const row = rows[stage] ??= { wallMs: wall[stage] ?? null, frames: 0, cpuMs: 0, stallMs: 0, longestMs: 0, sliceMs: 0, builds: 0 };
    row.frames++; row.cpuMs += f.cpu; row.builds += f.builds;
    row.stallMs += Math.max(0, (f.interval ?? 0) - baselineInterval);
    row.longestMs = Math.max(row.longestMs, f.interval ?? 0);
    row.sliceMs += f.slices.reduce((a, b) => a + b, 0);
  }
  for (const stage of Object.keys(wall)) rows[stage] ??= { wallMs: wall[stage], frames: 0, cpuMs: 0, stallMs: 0, longestMs: 0, sliceMs: 0, builds: 0 };
  for (const row of Object.values(rows)) { row.cpuMs = r2(row.cpuMs); row.stallMs = r2(row.stallMs); row.longestMs = r2(row.longestMs); row.sliceMs = r2(row.sliceMs); }
  return rows;
}

/** Long-animation-frame entries (> 50 ms) overlapping a window: count, total, blocking, and the costliest scripts. */
export interface LoafRow { t: number; d: number; block: number; scripts: { d: number; fn: string; url: string; type: string }[] }
export function loafSummary(rows: LoafRow[], from: number, to: number, top = 5) {
  const inside = rows.filter(r => r.t <= to && r.t + r.d >= from);
  const scripts = new Map<string, number>();
  for (const r of inside) for (const s of r.scripts) {
    const key = `${s.type}:${s.fn || '?'}@${s.url || '?'}`;
    scripts.set(key, (scripts.get(key) ?? 0) + s.d);
  }
  return { count: inside.length, ms: r2(inside.reduce((a, r) => a + r.d, 0)), blockingMs: r2(inside.reduce((a, r) => a + r.block, 0)),
    longestMs: r2(Math.max(0, ...inside.map(r => r.d))),
    scripts: [...scripts].sort((a, b) => b[1] - a[1]).slice(0, top).map(([key, ms]) => ({ script: key, ms: r2(ms) })) };
}

/** Band and LOD switches of a fly run by kind, and the frames that carried them against all others. */
export function bandSummary(frames: LabFrame[], events: LabEvent[]) {
  const kinds: Record<string, number> = {};
  for (const e of events) if (/^(band\.|cut\.commit|cell\.install|batch\.merge|tile\.(full|preview|drop|hold|release))/.test(e.k)) kinds[e.k] = (kinds[e.k] ?? 0) + 1;
  const withBands = frames.filter(f => f.bands > 0), without = frames.filter(f => f.bands === 0);
  return { events: kinds, framesWithSwaps: withBands.length,
    swapFrameMs: dist(withBands.map(f => f.interval)), otherFrameMs: dist(without.map(f => f.interval)) };
}
