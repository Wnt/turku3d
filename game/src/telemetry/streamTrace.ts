// Tile, scenery and LOD streaming trace for performance captures (telemetry/capture.ts, tools/perf/tile_timeline.py).
//
// Streaming code reports lifecycle steps (`event`), timed stages (`begin` ... end) and main-thread work slices
// (`slice`) here unconditionally; while no capture runs every call is one boolean check. A capture switches the
// trace on, gives it the camera each frame (distances are measured to the event's tile at event time), and drains
// it with every batch. Events beyond the bounded buffer between drains are counted as dropped, never grown without
// limit; the recorder reports its own cost per frame.

/** One event row: time (ms, capture clock), kind, tile/cell id, camera distance (m, horizontal), camera height. */
export interface TraceEvent { t: number; k: string; id?: string; d?: number; h?: number; [field: string]: unknown }

/** Main-thread streaming work categories accumulated per frame. */
export const SLICES = ['parse', 'install', 'compile', 'upload', 'scenery', 'lod'] as const;
export type SliceKind = typeof SLICES[number];

const L0 = 250;
const R = (x: number, d = 1) => Math.round(x * 10 ** d) / 10 ** d;

/** Horizontal distance (m) from (x, z) to the square of tile id `t<level>_<i>_<j>` (engine metres). */
export function tileDistance(id: string, x: number, z: number) {
  const m = /^t(\d+)_(-?\d+)_(-?\d+)$/.exec(id);
  if (!m) return undefined;
  const size = L0 * 2 ** Number(m[1]), x0 = Number(m[2]) * size, z0 = Number(m[3]) * size;
  return Math.hypot(Math.max(x0 - x, 0, x - x0 - size), Math.max(z0 - z, 0, z - z0 - size));
}

export class StreamTrace {
  /** Set by the capture; every call below returns at once while false. */
  on = false;
  readonly capacity: number;
  private events: TraceEvent[] = [];
  private slices = new Float64Array(SLICES.length);
  private sliced = false;
  dropped = 0;
  /** Milliseconds spent inside trace calls since the last `takeCost`. */
  private cost = 0;
  private camera = { x: NaN, y: NaN, z: NaN };
  constructor(capacity = 20000) { this.capacity = capacity; }

  /** The capture's camera for this frame (engine metres). */
  setCamera(x: number, y: number, z: number) { this.camera.x = x; this.camera.y = y; this.camera.z = z; }

  event(k: string, id?: string, fields?: Record<string, unknown>) {
    if (!this.on) return;
    const t = performance.now();
    if (this.events.length >= this.capacity) { this.dropped++; this.cost += performance.now() - t; return; }
    const row: TraceEvent = { t: R(t), k };
    if (id !== undefined) {
      row.id = id;
      const { x, y, z } = this.camera, d = Number.isFinite(x) ? tileDistance(id, x, z) : undefined;
      if (d !== undefined) { row.d = Math.round(d); row.h = Math.round(y); }
    }
    if (fields) for (const key in fields) {
      const value = fields[key];
      row[key] = typeof value === 'number' ? R(value, 2) : value;
    }
    this.events.push(row);
    this.cost += performance.now() - t;
  }

  /** Start a timed stage; the returned function records `k` with `ms` (and its own fields) when the stage ends. */
  begin(k: string, id?: string, fields?: Record<string, unknown>): (more?: Record<string, unknown>) => void {
    if (!this.on) return noop;
    const start = performance.now();
    return more => this.event(k, id, { ...fields, ...more, ms: performance.now() - start });
  }

  /** Main-thread work of `kind` in this frame (ms); synchronous sections only. */
  slice(kind: SliceKind, ms: number) {
    if (!this.on) return;
    this.slices[SLICES.indexOf(kind)] += ms;
    this.sliced = true;
  }

  /** Time a synchronous section as a slice of `kind` and return its result. */
  time<T>(kind: SliceKind, run: () => T): T {
    if (!this.on) return run();
    const start = performance.now();
    try { return run(); } finally { this.slice(kind, performance.now() - start); }
  }

  /** This frame's slices (ms per SLICES entry) or null when no streaming work ran; resets them. */
  takeSlices(): number[] | null {
    if (!this.sliced) return null;
    const row = Array.from(this.slices, v => R(v, 2));
    this.slices.fill(0); this.sliced = false;
    return row;
  }

  /** Milliseconds the trace itself spent since the previous call. */
  takeCost() { const cost = this.cost; this.cost = 0; return cost; }

  drain(): TraceEvent[] {
    const events = this.events;
    this.events = [];
    return events;
  }

  reset() { this.events = []; this.slices.fill(0); this.sliced = false; this.dropped = 0; this.cost = 0; }
}

const noop = () => {};

/** The game's one trace, shared by streaming code and the capture. */
export const streamTrace = new StreamTrace();
