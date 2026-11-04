// Per-lane drive trace and pass/fail rules for the bridge and underpass sweep (tools/drivetest/README.md).
// Fed once per physics step with the driven vehicle's state; works the same in Node (physics/world.ts directly)
// and in the browser (the worker's snapshots), since both carry the same VehicleState.
import type { VehicleState } from '../../physics/protocol';

/** Pass/fail limits. A smooth road in a commercial driving game: wheels stay planted, the chassis never jolts, the
 *  ground has no visible steps and the car never touches a railing or kerb on its own lane. */
export const LIMITS = {
  /** A wheel off the ground this long (s) at driving speed. Real decks never unload a wheel; one physics step
   *  (17 ms) is solver noise, three are a crest or a hole the player feels. */
  contactLoss: 0.05,
  /** Vertical chassis acceleration (m/s², 83 ms mean). 0.3 g is a speed hump at 30 km/h; a sag or crest on a
   *  well-built deck at the limit stays near 0.1 g. Warn from 0.15 g. */
  vertical: { warn: 1.5, fail: 3.0 },
  /** Vertical jerk (m/s³) of the acceleration over 167 ms: 0.3 g within 50 ms is a hit (60 m/s³ at that
   *  resolution); 0.5 g within 50 ms (100) fails. Trimesh contact noise on a flat deck stays below 1. */
  jerk: { warn: 50, fail: 100 },
  /** Pitch and roll rates (rad/s, 83 ms mean). A 6 % grade change over 10 m at 50 km/h pitches about 0.08 rad/s;
   *  0.35 rad/s (20°/s) is a lurch the chase camera shows. */
  rate: { warn: 0.2, fail: 0.35 },
  /** A step in the ground under a wheel track (m, within 20 cm of travel). Expansion joints stand < 2 cm proud;
   *  the game's own bump detector (vehicle/bumps.ts) and kerb rumble start at 5 cm. */
  step: { warn: 0.025, fail: 0.04 },
  /** Suspension compression speed (m/s, VehicleState.bump.jolt): the haptics "jolt" starts at 1 m/s. */
  jolt: { warn: 0.5, fail: 1.0 },
  /** Chassis speed change from a collision (m/s, impact impulse / mass): the haptics collision floor. */
  impact: 0.5,
  /** Chassis below the lane surface by this much (m): fell through. */
  fall: 1.5,
} as const;

export type Severity = 'pass' | 'warn' | 'fail';
export interface TraceEvent {
  kind: 'contact' | 'vertical' | 'jerk' | 'pitch' | 'roll' | 'step' | 'jolt' | 'impact' | 'curb' | 'fall' | 'stuck' | 'incomplete';
  severity: Severity; value: number; s: number; x: number; y: number; z: number; detail?: string;
}
export interface TraceSample { t: number; s: number; x: number; y: number; z: number; v: number; ay: number; jerk: number; pitchRate: number; rollRate: number; off: number }

const SMOOTH = 5;
/** Jerk is the change of the 83 ms mean over 50 ms (3 steps). */
const JERK_LAG = 3;
const rank: Record<Severity, number> = { pass: 0, warn: 1, fail: 2 };
const level = (value: number, l: { warn: number; fail: number }): Severity => value >= l.fail ? 'fail' : value >= l.warn ? 'warn' : 'pass';

/** Chassis forward, up and right (world) from the body quaternion (chassis +X forward, +Y up, +Z right). */
function axes(q: { x: number; y: number; z: number; w: number }) {
  const { x, y, z, w } = q;
  return {
    fy: 2 * (x * y + w * z),
    ry: 2 * (y * z - w * x),
  };
}

export class DriveTrace {
  readonly samples: TraceSample[] = [];
  readonly events: TraceEvent[] = [];
  private prev: { vy: number; pitch: number; roll: number } | null = null;
  private ays: number[] = []; private pitches: number[] = []; private rolls: number[] = [];
  private ayHistory: number[] = [];
  private offFor: number[] = [];
  private bumpId = -1; private impactId = -1; private strikeId = -1;
  private t = 0;
  frameMs: number[] = [];

  /** `window`: the arc range [from, to] on the path that counts (the structure plus its joints). */
  constructor(readonly window: [number, number], readonly mass = 1200) {}

  private event(e: Omit<TraceEvent, 'severity'> & { severity?: Severity }) {
    if ((e.severity ?? 'fail') === 'pass') return;
    this.events.push({ severity: 'fail', ...e } as TraceEvent);
  }

  /** One physics step. `s` the path arc length, `surfaceY` the lane's surface height there (or null). */
  step(v: VehicleState, dt: number, s: number, surfaceY: number | null) {
    this.t += dt;
    const inside = s >= this.window[0] && s <= this.window[1];
    const { fy, ry } = axes(v.rot);
    const pitch = Math.asin(Math.max(-1, Math.min(1, fy))), roll = Math.asin(Math.max(-1, Math.min(1, ry)));
    const p = { x: v.pos.x, y: v.pos.y, z: v.pos.z };
    // Bump, impact and curb events carry increasing ids; only new ones within the window count.
    if (v.bump && v.bump.id !== this.bumpId) {
      if (this.bumpId >= 0 && inside) {
        if (Math.abs(v.bump.height) > 0) this.event({ kind: 'step', value: Math.abs(v.bump.height), s, ...p, severity: level(Math.abs(v.bump.height), LIMITS.step), detail: `wheel ${v.bump.wheel} bump detector` });
        if (v.bump.jolt > 0) this.event({ kind: 'jolt', value: v.bump.jolt, s, ...p, severity: level(v.bump.jolt, LIMITS.jolt), detail: `wheel ${v.bump.wheel}` });
      }
      this.bumpId = v.bump.id;
    }
    if (v.impact && v.impact.id !== this.impactId) {
      const dv = v.impact.impulse / this.mass;
      if (this.impactId >= 0 && inside && dv >= LIMITS.impact) this.event({ kind: 'impact', value: dv, s, ...p });
      this.impactId = v.impact.id;
    }
    if (v.curbStrike && v.curbStrike.id !== this.strikeId) {
      if (this.strikeId >= 0 && inside) this.event({ kind: 'curb', value: v.curbStrike.height, s, ...p, detail: `wheel ${v.curbStrike.wheel}${v.curbStrike.blocked ? ' blocked' : ''}` });
      this.strikeId = v.curbStrike.id;
    }
    if (this.bumpId < 0) this.bumpId = v.bump?.id ?? 0;
    if (this.impactId < 0) this.impactId = v.impact?.id ?? 0;
    if (this.strikeId < 0) this.strikeId = v.curbStrike?.id ?? 0;

    let ay = 0, jerk = 0, pitchRate = 0, rollRate = 0;
    if (this.prev) {
      push(this.ays, (v.linvel.y - this.prev.vy) / dt);
      push(this.pitches, (pitch - this.prev.pitch) / dt);
      push(this.rolls, (roll - this.prev.roll) / dt);
      ay = mean(this.ays); pitchRate = mean(this.pitches); rollRate = mean(this.rolls);
      if (this.ays.length === SMOOTH) {
        this.ayHistory.push(ay);
        if (this.ayHistory.length > JERK_LAG + 1) this.ayHistory.shift();
        if (this.ayHistory.length === JERK_LAG + 1) jerk = (ay - this.ayHistory[0]) / (JERK_LAG * dt);
      }
    }
    this.prev = { vy: v.linvel.y, pitch, roll };
    let off = 0;
    v.wheels.forEach((w, i) => {
      this.offFor[i] = w.contact ? 0 : (this.offFor[i] ?? 0) + dt;
      off = Math.max(off, this.offFor[i]);
    });
    this.samples.push({ t: this.t, s, ...p, v: v.speed, ay, jerk, pitchRate, rollRate, off });
    if (!inside || this.ays.length < SMOOTH || v.speed < 2) return;
    this.peak('vertical', Math.abs(ay), LIMITS.vertical, s, p);
    this.peak('jerk', Math.abs(jerk), LIMITS.jerk, s, p);
    this.peak('pitch', Math.abs(pitchRate), LIMITS.rate, s, p);
    this.peak('roll', Math.abs(rollRate), LIMITS.rate, s, p);
    if (off >= LIMITS.contactLoss) this.peak('contact', off, { warn: LIMITS.contactLoss, fail: LIMITS.contactLoss }, s, p);
    if (surfaceY !== null && v.pos.y < surfaceY - LIMITS.fall) this.peak('fall', surfaceY - v.pos.y, { warn: LIMITS.fall, fail: LIMITS.fall }, s, p);
  }

  /** One event per contiguous exceedance, at its peak. */
  private open = new Map<string, TraceEvent>();
  private peak(kind: TraceEvent['kind'], value: number, l: { warn: number; fail: number }, s: number, p: { x: number; y: number; z: number }) {
    const severity = level(value, l), current = this.open.get(kind);
    if (severity === 'pass') { this.open.delete(kind); return; }
    if (current && s - current.s < 3) {
      if (value > current.value) Object.assign(current, { value, s, ...p, severity: rank[severity] > rank[current.severity] ? severity : current.severity });
      return;
    }
    const e: TraceEvent = { kind, severity, value, s, ...p };
    this.events.push(e); this.open.set(kind, e);
  }

  addGround(steps: { s: number; x: number; y: number; z: number; height: number; track: string }[]) {
    for (const g of steps) {
      if (g.s < this.window[0] || g.s > this.window[1]) continue;
      const severity = level(g.height, LIMITS.step);
      if (severity !== 'pass') this.events.push({ kind: 'step', severity, value: g.height, s: g.s, x: g.x, y: g.y, z: g.z, detail: `${g.track} wheel track, collision profile` });
    }
  }

  fail(kind: 'stuck' | 'incomplete', detail: string, s: number) {
    const at = this.samples.at(-1) ?? { x: 0, y: 0, z: 0 };
    this.events.push({ kind, severity: 'fail', value: 1, s, x: at.x, y: at.y, z: at.z, detail });
  }

  summary() {
    const inWindow = this.samples.filter(p => p.s >= this.window[0] && p.s <= this.window[1]);
    const max = (f: (p: TraceSample) => number) => inWindow.reduce((m, p) => Math.max(m, Math.abs(f(p))), 0);
    const severity: Severity = this.events.reduce<Severity>((w, e) => rank[e.severity] > rank[w] ? e.severity : w, 'pass');
    const worst = [...this.events].sort((a, b) => rank[b.severity] - rank[a.severity] || score(b) - score(a))[0] ?? null;
    const frames = [...this.frameMs].sort((a, b) => a - b);
    return {
      severity, worst,
      peaks: { vertical: max(p => p.ay), jerk: max(p => p.jerk), pitchRate: max(p => p.pitchRate), rollRate: max(p => p.rollRate),
        contactLoss: max(p => p.off), speed: inWindow.length ? inWindow.reduce((a, p) => a + p.v, 0) / inWindow.length : 0 },
      events: this.events,
      frameP95: frames.length ? frames[Math.floor(frames.length * 0.95)] : null,
    };
  }
}

/** Events of different kinds ranked by how far past their own fail limit they are. */
function score(e: TraceEvent): number {
  const l: Record<string, number> = { vertical: LIMITS.vertical.fail, jerk: LIMITS.jerk.fail, pitch: LIMITS.rate.fail, roll: LIMITS.rate.fail,
    step: LIMITS.step.fail, jolt: LIMITS.jolt.fail, impact: LIMITS.impact, contact: LIMITS.contactLoss, fall: 0.5, curb: 0.03, stuck: 0.2, incomplete: 0.2 };
  return e.value / (l[e.kind] ?? 1) + (e.kind === 'fall' || e.kind === 'stuck' || e.kind === 'incomplete' ? 100 : 0);
}

function push(list: number[], v: number) { list.push(v); if (list.length > SMOOTH) list.shift(); }
function mean(list: number[]) { let s = 0; for (const v of list) s += v; return list.length ? s / list.length : 0; }
