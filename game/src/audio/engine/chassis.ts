import type { WheelState } from '../../physics/protocol';
import { MATERIALS } from '../../shared/col/materials';
import { Biquad, OnePole, Rng, clamp, dbToGain } from './dsp';
import { SILENT_TYRES, TYRE_KINDS, TyreSynth, TyreTracker, type TyreFrame } from './tyre';

/** Trophy-truck chassis and tyre sounds (SPEC §4.4-4.5), synthesised by us. `ChassisTracker` derives events and
 *  continuous levels from the synced wheel state only (identically for the local and remote trucks);
 *  `ChassisSynth` renders them inside the engine voice, so they share its position. */

export interface ChassisSpec {
  /** Static suspension length (m) and the travel from static, front then rear axle. */
  staticLength: number; bump: readonly [number, number]; droop: readonly [number, number];
  /** Rolling radius (m) and tread pitches around the tyre (the lug-pass tone). */
  radius: number; pitches: number;
}
/** SPEC §3.3 / §1.2: L0 0.417, bump 0.36/0.38, droop 0.30/0.33, 42" tyre with 36 pitches. */
export const TROPHY_CHASSIS: ChassisSpec = { staticLength: 0.417, bump: [0.36, 0.38], droop: [0.30, 0.33], radius: 0.533, pitches: 36 };

export type HitKind = 'thump' | 'knock' | 'clack';
export interface ChassisHit { kind: HitKind; wheel: number; gain: number }
export interface ChassisFrame {
  hits: ChassisHit[];
  /** Asphalt knobby hum level 0..1 and its lug-pass frequency (Hz). */
  hum: number; lugHz: number;
  /** Gravel/sand roost, soil/grass bed, body rattle (0..1). */
  gravel: number; soil: number; rattle: number;
  /** The tyres' slide, load and surface (tyre.ts): the stick-slip squeal and scrub. */
  tyres: TyreFrame;
  /** Rear-steer hydraulic whine level 0..1 and pitch (Hz). */
  whine: number; whineHz: number;
}
export const SILENT_FRAME: ChassisFrame = { hits: [], hum: 0, lugHz: 0, gravel: 0, soil: 0, rattle: 0, tyres: SILENT_TYRES, whine: 0, whineHz: 1200 };

export type SurfaceClass = 'asphalt' | 'gravel' | 'soil' | 'none';
/** The tyre surface class of a ground material, from the same `MATERIALS[..].audio` key the grip table uses. */
export function surfaceClass(material: number | undefined): SurfaceClass {
  const audio = material === undefined ? 'asphalt' : MATERIALS[material]?.audio ?? 'asphalt';
  if (audio === 'gravel' || audio === 'sand') return 'gravel';
  if (audio === 'soil' || audio === 'grass') return 'soil';
  if (audio === 'water' || audio === 'none') return 'none';
  return 'asphalt';
}

/** Lug-pass frequency of the knobby tread at `speed` m/s: pitches · v / (2π r) (≈ 215 Hz at 20 m/s). */
export const lugHz = (speed: number, spec: ChassisSpec = TROPHY_CHASSIS) => spec.pitches * Math.abs(speed) / (2 * Math.PI * spec.radius);

/** Landing gain from the compression velocity: 0 at 1.5 m/s, 1 at 6 m/s. */
export const thumpGain = (compressionVelocity: number) => clamp((compressionVelocity - 1.5) / 4.5);

export interface ChassisInput { dt: number; wheels: readonly WheelState[]; speed: number;
  /** Rear-steer slew (rad/s). */
  rearSlew: number;
  /** Chassis height (m, world); its acceleration drives the body rattle. */
  heave?: number }

const DEBOUNCE = 0.15, WINDOW = 0.04;

export class ChassisTracker {
  private readonly last = new Float64Array(4).fill(NaN);
  private readonly lastVelocity = new Float64Array(4);
  private readonly contact = [true, true, true, true];
  private readonly armed = [0, 0, 0, 0];
  private readonly peak = new Float64Array(4);
  private readonly quiet = new Float64Array(4).fill(1);
  private readonly cooldown = new Float64Array(4);
  private readonly stopCooldown = new Float64Array(4);
  private readonly tyres = new TyreTracker('truck');
  private heave: number[] = [];
  private accelSq = 0;

  constructor(private readonly spec: ChassisSpec = TROPHY_CHASSIS) {}

  step(input: ChassisInput): ChassisFrame {
    const dt = clamp(input.dt, 1e-4, 0.25), speed = Math.abs(Number.isFinite(input.speed) ? input.speed : 0), sp = this.spec;
    const hits: ChassisHit[] = [];
    const counts = { asphalt: 0, gravel: 0, soil: 0, none: 0 };
    let slipSum = 0, suspAccel = 0;
    for (let i = 0; i < 4; i++) {
      const w = input.wheels[i];
      if (!w || !Number.isFinite(w.suspension)) { this.last[i] = NaN; this.lastVelocity[i] = 0; continue; }
      const prev = this.last[i];
      const velocity = Number.isFinite(prev) ? (prev - w.suspension) / dt : 0; // + compressing
      this.last[i] = w.suspension;
      const axle = i < 2 ? 0 : 1, b = sp.staticLength - w.suspension;
      const bumpFrac = b / sp.bump[axle], droopFrac = -b / sp.droop[axle];
      this.cooldown[i] -= dt; this.stopCooldown[i] -= dt;
      // Landing thump: on gaining contact or a compression faster than 1.5 m/s; the peak over a 40 ms window sets
      // the gain; it re-arms after 150 ms once the compression has slowed below 1 m/s (one thump per landing).
      const gained = w.contact && !this.contact[i];
      this.contact[i] = w.contact;
      if (velocity < 1) this.quiet[i] = 1;
      if (this.armed[i] > 0) {
        this.peak[i] = Math.max(this.peak[i], velocity);
        this.armed[i] -= dt;
        if (this.armed[i] <= 0) {
          const gain = thumpGain(this.peak[i]);
          if (gain > 0) { hits.push({ kind: 'thump', wheel: i, gain }); this.cooldown[i] = DEBOUNCE; this.quiet[i] = 0; }
        }
      } else if (this.cooldown[i] <= 0 && this.quiet[i] && (gained || velocity > 1.5)) {
        this.armed[i] = WINDOW; this.peak[i] = velocity;
      }
      if (this.stopCooldown[i] <= 0) {
        if (bumpFrac > 0.88 && velocity > 2) { hits.push({ kind: 'knock', wheel: i, gain: clamp((velocity - 2) / 3, 0.3, 1) }); this.stopCooldown[i] = DEBOUNCE; }
        else if (droopFrac > 0.9 && -velocity > 1.5) { hits.push({ kind: 'clack', wheel: i, gain: clamp((-velocity - 1.5) / 3, 0.3, 1) }); this.stopCooldown[i] = DEBOUNCE; }
      }
      suspAccel += Math.abs(velocity - this.lastVelocity[i]) / dt; this.lastVelocity[i] = velocity;
      if (w.contact) {
        counts[surfaceClass(w.tyre?.material)]++;
        const t = w.tyre;
        slipSum += t && Number.isFinite(t.lateral) && Number.isFinite(t.longitudinal) ? clamp(Math.hypot(t.lateral, t.longitudinal) / 6) : 0;
      }
    }
    // Body rattle from the chassis' vertical acceleration (RMS over ≈ 0.3 s).
    const heave = Number.isFinite(input.heave) ? input.heave! : NaN;
    let accel = 0;
    if (Number.isFinite(heave)) {
      this.heave.push(heave); if (this.heave.length > 3) this.heave.shift();
      if (this.heave.length === 3) accel = (this.heave[2] - 2 * this.heave[1] + this.heave[0]) / (dt * dt);
    }
    // Wheel-relative accelerations shake the cage as well (remote trucks without a heave sample still rattle).
    accel = Math.max(Math.abs(accel), suspAccel / 4 * 0.5);
    this.accelSq += (Math.min(accel, 80) ** 2 - this.accelSq) * (1 - Math.exp(-dt / 0.3));
    const contacts = counts.asphalt + counts.gravel + counts.soil + counts.none;
    const share = (n: number) => (contacts ? n / contacts : 0);
    const slip = contacts ? slipSum / contacts : 0;
    const roost = clamp((slip * speed + 0.2 * speed) / 10);
    const slew = Math.abs(Number.isFinite(input.rearSlew) ? input.rearSlew : 0);
    return {
      hits,
      hum: share(counts.asphalt) * clamp((speed - 5) / 25), lugHz: lugHz(speed, sp),
      gravel: share(counts.gravel) * roost, soil: share(counts.soil) * roost * 0.85,
      rattle: clamp(Math.sqrt(this.accelSq) / 25),
      tyres: this.tyres.step(dt, input.wheels, speed),
      whine: slew > 0.1 ? clamp((slew - 0.1) / 0.6) : 0, whineHz: 1100 + 300 * clamp(slew / 1.4),
    };
  }
}

interface Hit { kind: HitKind; age: number; length: number; amp: number; phase: number; lp: OnePole }

/** Renders a ChassisFrame per control tick; `render` adds into the block. */
export class ChassisSynth {
  private readonly rng: Rng;
  private readonly hits: Hit[];
  private target: ChassisFrame = SILENT_FRAME;
  private cur = { hum: 0, lugHz: 0, gravel: 0, soil: 0, rattle: 0, whine: 0, whineHz: 1200 };
  private humPhase = 0; private whinePhase = 0;
  private readonly tyre: TyreSynth;
  private readonly humBp = new Biquad(); private readonly whineBp = new Biquad();
  private readonly gravelHp: Biquad; private readonly gravelLp: Biquad;
  private readonly soilHp: Biquad; private readonly soilLp: Biquad;
  private readonly rattleBp: Biquad; private readonly tickBp: Biquad;
  private grain = 0; private grainIn = 0; private tick = 0; private tickIn = 0;
  private lastLug = -1;
  /** Hits started so far, by kind (tests and the snapshot). */
  readonly counts: Record<HitKind, number> = { thump: 0, knock: 0, clack: 0 };

  constructor(seed: number, private readonly sampleRate = 48000) {
    const sr = sampleRate;
    this.rng = new Rng(seed);
    this.hits = Array.from({ length: 8 }, () => ({ kind: 'thump' as HitKind, age: 0, length: 0, amp: 0, phase: 0, lp: new OnePole().lowpass(2500, sr) }));
    this.gravelHp = new Biquad().highpass(2000, Math.SQRT1_2, sr); this.gravelLp = new Biquad().lowpass(8000, Math.SQRT1_2, sr);
    this.soilHp = new Biquad().highpass(300, Math.SQRT1_2, sr); this.soilLp = new Biquad().lowpass(1500, Math.SQRT1_2, sr);
    this.rattleBp = new Biquad().bandpass(550, 1.2, sr);
    this.tickBp = new Biquad().bandpass(4500, 1.5, sr);
    this.whineBp.bandpass(1250, 20, sr);
    // The squeal and scrub of the 42" tyres; rolling noise and loose surfaces are the hum, roost and soil layers here.
    this.tyre = new TyreSynth(sr, seed + 7, { pitch: TYRE_KINDS.truck.pitch, roll: 0, grit: false });
  }

  frame(f: ChassisFrame) {
    this.target = f; this.tyre.frame(f.tyres);
    for (const h of f.hits) {
      const slot = this.hits.find(x => x.age >= x.length) ?? this.hits[0];
      slot.kind = h.kind; slot.age = 0; slot.phase = 0;
      slot.length = Math.round((h.kind === 'thump' ? 0.32 : h.kind === 'knock' ? 0.16 : 0.06) * this.sampleRate);
      slot.amp = clamp(h.gain) * (h.kind === 'thump' ? 0.55 : h.kind === 'knock' ? 0.32 : 0.1) * this.rng.range(0.85, 1.15);
      this.counts[h.kind]++;
    }
  }

  render(out: Float32Array) {
    const sr = this.sampleRate, n = out.length, t = this.target, c = this.cur;
    const k = 1 - Math.exp(-n / (0.05 * sr));
    c.hum += (t.hum - c.hum) * k; c.gravel += (t.gravel - c.gravel) * k; c.soil += (t.soil - c.soil) * k;
    c.rattle += (t.rattle - c.rattle) * k; c.whine += (t.whine - c.whine) * k;
    c.lugHz += (t.lugHz - c.lugHz) * Math.min(1, k * 2); c.whineHz += (t.whineHz - c.whineHz) * k;
    if (Math.abs(c.lugHz - this.lastLug) > 1 && c.lugHz > 20) { this.humBp.bandpass(c.lugHz, 8, sr); this.lastLug = c.lugHz; }
    const hum = c.hum * 0.05, gravel = c.gravel * 0.09, soil = c.soil * 0.08;
    // Rear-steer whine: −30 dB re the idle engine (FULL_SCALE·−16 dB in v8core).
    const whine = c.whine * 0.32 * dbToGain(-16) * dbToGain(-30) * 1.4;
    const grainRate = c.rattle * 30, tickRate = c.gravel * 70;
    this.tyre.render(out, TYRE_KINDS.truck.gain);
    const active = hum + gravel + soil + whine + c.rattle > 1e-5 || this.hits.some(h => h.age < h.length);
    if (!active) return;
    const hitsLive = this.hits.some(h => h.age < h.length);
    for (let i = 0; i < n; i++) {
      let y = 0;
      const noise = this.rng.bi();
      if (hum > 1e-5) {
        this.humPhase += c.lugHz / sr; if (this.humPhase > 1) this.humPhase -= 1;
        const p = 2 * Math.PI * this.humPhase;
        y += hum * (Math.sin(p) + 0.5 * Math.sin(2 * p) + 0.3 * Math.sin(3 * p) + 1.5 * this.humBp.process(noise));
      }
      if (gravel > 1e-5) {
        y += gravel * this.gravelLp.process(this.gravelHp.process(noise));
        if (--this.tickIn <= 0 && tickRate > 0.5) { this.tick = Math.round(this.rng.range(0.001, 0.003) * sr); this.tickIn = sr / tickRate * this.rng.range(0.3, 1.7); }
        if (this.tick > 0) { y += gravel * 3 * this.tickBp.process(this.rng.bi()); this.tick--; }
      }
      if (soil > 1e-5) y += soil * this.soilLp.process(this.soilHp.process(noise)) * 1.6;
      if (c.rattle > 0.02) {
        if (--this.grainIn <= 0 && grainRate > 0.5) { this.grain = Math.round(this.rng.range(0.005, 0.015) * sr); this.grainIn = sr / grainRate * this.rng.range(0.3, 1.7); }
        if (this.grain > 0) { y += 0.035 * c.rattle * this.rattleBp.process(this.rng.bi()) * 3; this.grain--; }
      }
      if (whine > 1e-6) {
        this.whinePhase += c.whineHz / sr; if (this.whinePhase > 1) this.whinePhase -= 1;
        y += whine * (Math.sin(2 * Math.PI * this.whinePhase) + 2 * this.whineBp.process(noise));
      }
      for (let hi = 0; hitsLive && hi < this.hits.length; hi++) {
        const h = this.hits[hi];
        if (h.age >= h.length) continue;
        const s = h.age / sr;
        let v: number;
        if (h.kind === 'thump') {
          // 120 → 45 Hz exponential sweep over 120 ms, plus a short click.
          const f = 45 + 75 * Math.exp(-s / 0.045);
          h.phase += f / sr;
          v = Math.sin(2 * Math.PI * h.phase) * Math.min(1, s / 0.002) * Math.exp(-s / 0.07)
            + h.lp.process(this.rng.bi()) * Math.exp(-s / 0.004) * 0.6;
        } else if (h.kind === 'knock') {
          h.phase += 90 / sr;
          v = Math.sin(2 * Math.PI * h.phase) * Math.min(1, s / 0.001) * Math.exp(-s / 0.035) + h.lp.process(this.rng.bi()) * Math.exp(-s / 0.006) * 0.4;
        } else {
          h.phase += 250 / sr;
          v = Math.sin(2 * Math.PI * h.phase) * Math.exp(-s / 0.012) + this.rng.bi() * Math.exp(-s / 0.0015) * 0.5;
        }
        y += v * h.amp; h.age++;
      }
      out[i] += y;
    }
  }
}
