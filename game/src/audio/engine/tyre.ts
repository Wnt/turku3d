import type { VehicleKind } from '../../physics/constants';
import type { WheelState } from '../../physics/protocol';
import { MATERIALS } from '../../shared/col/materials';
import { Biquad, Rng, clamp } from './dsp';

/** Tyre sound of every vehicle: stick-slip squeal, scrub, rolling noise and loose-surface grit, synthesised by us
 *  (CC0). `TyreTracker` turns synced wheel states into one control frame, identically for the local and remote
 *  vehicles; `TyreSynth` renders it in an AudioWorklet (tyre.worklet.ts for cars and scooters, the truck's engine voice
 *  through ChassisSynth).
 *
 *  The squeal is a physical model: five tread blocks (a mass on a spring, dragged by the road at the sliding speed)
 *  with Coulomb/Stribeck friction and explicit stick and slip states. While a block sticks it moves with the road; when
 *  the spring force passes static friction it snaps back and is caught again. Its motion is the sound, so pitch,
 *  loudness, the chirp when a slide starts and the falling howl as it ends all follow from sliding speed and load:
 *    stick time = 2·Δμ·N / (ω²·V): a faster slide or a lighter tyre sings higher, up to the block's own resonance;
 *    Δμ (static minus sliding friction) is the surface: asphalt and smooth concrete squeal, gravel and grass do not.
 *  Block resonances, harmonic levels and the noise floor were calibrated against CC0 recordings of real squeals
 *  (BigSoundBank 2368-2371; measured offline, no recording is in the repository or the synthesis). */

/** One control frame (per physics tick). `slide`: sliding speed of the hardest-sliding contact patch (m/s), already
 *  faded in from below the grip limit; `load`: that tyre's normal force relative to rest; `speed`: vehicle speed over
 *  the ground (m/s) while any tyre touches, for the rolling noise; `squeal`: squeal tendency of the surface under the
 *  sliding tyre (0 none, 1 asphalt); `rough`: road roughness 0..1; `grit`: loose-surface share 0..1. */
export interface TyreFrame { slide: number; load: number; speed: number; squeal: number; rough: number; grit: number }
export const SILENT_TYRES: TyreFrame = { slide: 0, load: 1, speed: 0, squeal: 1, rough: 0.35, grit: 0 };

/** Surface character by the material table's `audio` key (shared/col/materials.ts). */
const SURFACES: Record<string, { squeal: number; rough: number; grit: number }> = {
  asphalt: { squeal: 1, rough: 0.35, grit: 0 }, concrete: { squeal: 1.15, rough: 0.15, grit: 0 }, roof: { squeal: 1.15, rough: 0.15, grit: 0 },
  stone: { squeal: 0.8, rough: 0.6, grit: 0 }, metal: { squeal: 1.2, rough: 0.1, grit: 0 }, wood: { squeal: 0.5, rough: 0.5, grit: 0 },
  gravel: { squeal: 0, rough: 0.9, grit: 1 }, sand: { squeal: 0, rough: 0.7, grit: 0.6 }, soil: { squeal: 0, rough: 0.8, grit: 0.3 },
  grass: { squeal: 0, rough: 0.7, grit: 0.15 },
};

/** Per vehicle class: resonance scale (a small tyre sings higher, the truck's 42" knobbies lower), output gain (a full
 *  slide at 8 m/s renders 0.31 RMS at gain 1; 0.36 puts it at the level the sample loop it replaced peaked at),
 *  rolling-noise gain (1.6 matches the old loop at 24 m/s; the truck's lug hum, roost and soil bed are ChassisSynth
 *  layers), and the suspension travel (m) that doubles a tyre's load. */
export const TYRE_KINDS: Record<VehicleKind, { pitch: number; gain: number; roll: number; travel: number }> = {
  car: { pitch: 1, gain: 0.36, roll: 1.6, travel: 0.07 },
  scooter: { pitch: 1.18, gain: 0.22, roll: 0.9, travel: 0.05 },
  truck: { pitch: 0.8, gain: 0.22, roll: 0, travel: 0.3 },
};

const smoothstep = (a: number, b: number, x: number) => { const t = clamp((x - a) / (b - a)); return t * t * (3 - 2 * t); };

/** The sliding speed (m/s) a tyre sounds with: the contact patch's slip velocity, faded in between 70 % and 115 % of
 *  the speed-scaled limit at which tyre marks start (vehicle/tyreSlip.ts tyreStrength): a tyre sings a little before
 *  it marks, and the small slip of ordinary driving (the marks' "straight-road disturbances") stays silent. */
export function slideSpeed(lateral: number, longitudinal: number, speed: number): number {
  if (!Number.isFinite(lateral) || !Number.isFinite(longitudinal)) return 0;
  const v = Math.min(30, Math.abs(Number.isFinite(speed) ? speed : 0));
  const demand = Math.hypot(lateral / (2.2 + v * 0.05), longitudinal / (2.8 + v * 0.06));
  return Math.min(30, Math.hypot(lateral, longitudinal)) * smoothstep(0.7, 1.15, demand);
}

/** A wheel's slide counts once its contact has settled for SETTLE seconds (the tyre marks' rule, tyreSlip.ts
 *  TyreSlipFilter: the physics reports large slip for a few ticks after a landing or a bump, and suspension travel
 *  faster than JOLT m/s restarts the wait) and it has then slid for ONSET seconds without a break. The marks wait
 *  180 ms; the sound waits 50 ms, so a chirp is heard. */
const SETTLE = 0.08, JOLT = 1.5, ONSET = 0.05;

/** Derives the tyre frame of one vehicle from its wheel states (the same wire data the tyre marks use). */
export class TyreTracker {
  /** Slow mean of each wheel's suspension length while it touches: its rest length under this car's weight. */
  private readonly rest = new Float64Array(8).fill(NaN);
  private readonly last = new Float64Array(8).fill(NaN);
  private readonly settled = new Float64Array(8);
  private readonly sliding = new Float64Array(8);
  constructor(private readonly kind: VehicleKind = 'car') {}

  step(dt: number, wheels: readonly WheelState[], speed: number): TyreFrame {
    dt = clamp(dt, 1e-4, 0.25);
    const travel = TYRE_KINDS[this.kind].travel, settle = 1 - Math.exp(-dt / 3);
    let contacts = 0, rough = 0, grit = 0, tone = 0, toneLoad = 1, toneSqueal = 1, loose = 0, looseLoad = 1;
    for (let i = 0; i < wheels.length && i < this.rest.length; i++) {
      const w = wheels[i], t = w?.tyre;
      const surface = w?.contact && t && Number.isFinite(w.suspension) ? SURFACES[MATERIALS[t.material]?.audio ?? 'asphalt'] : undefined;
      if (!w || !t || !surface) { this.last[i] = NaN; this.settled[i] = this.sliding[i] = 0; continue; } // airborne, water, boundaries
      const jolt = !Number.isFinite(this.last[i]) || Math.abs(w.suspension - this.last[i]) / dt > JOLT;
      this.last[i] = w.suspension;
      this.settled[i] = jolt ? 0 : this.settled[i] + dt;
      if (!Number.isFinite(this.rest[i])) this.rest[i] = w.suspension;
      this.rest[i] += (w.suspension - this.rest[i]) * settle;
      const load = clamp(1 + (this.rest[i] - w.suspension) / travel, 0.3, 2.3);
      let slide = this.settled[i] >= SETTLE ? slideSpeed(t.lateral, t.longitudinal, speed) : 0;
      this.sliding[i] = slide > 0 ? this.sliding[i] + dt : 0;
      if (this.sliding[i] < ONSET) slide = 0;
      contacts++; rough += surface.rough; grit += surface.grit;
      if (surface.squeal > 0) { if (slide > tone) { tone = slide; toneLoad = load; toneSqueal = surface.squeal; } }
      else if (slide > loose) { loose = slide; looseLoad = load; }
    }
    if (!contacts) return SILENT_TYRES;
    return { slide: tone > 0 ? tone : loose, load: tone > 0 ? toneLoad : looseLoad, squeal: tone > 0 ? toneSqueal : 0,
      speed: Math.min(60, Math.abs(Number.isFinite(speed) ? speed : 0)), rough: rough / contacts, grit: grit / contacts };
  }
}

/** Tread-block resonances (Hz) and their share of the load. Measured squeals hold three or four tones 60-120 Hz apart
 *  between 0.85 and 1.27 kHz, which beat against each other at tens of hertz. */
const BLOCKS: readonly (readonly [hz: number, share: number])[] = [[1085, 0.55], [1190, 0.36], [1140, 0.5], [1010, 0.3], [1255, 0.26]];
const OVERSAMPLE = 4;
/** Normal force per unit block mass at rest (m/s²): sets the sliding speed at which the pitch reaches the resonance. */
const N0 = 5.2e4;
const MU_STATIC = 1, STRIBECK = 5.5;

interface Block { hz: number; share: number; zeta: number; x: number; v: number; stuck: boolean; n1: number; n2: number;
  wander: number; wanderTarget: number; last: number; k: number; c2: number }

export interface TyreSynthOptions { pitch?: number; roll?: number; grit?: boolean }

/** Renders tyre frames; `render` adds into the block. Idle (no slide, standing still) it costs nothing. */
export class TyreSynth {
  private readonly rng: Rng;
  private readonly blocks: Block[];
  private target: TyreFrame = SILENT_TYRES;
  private readonly cur = { slide: 0, load: 1, speed: 0, squeal: 1, rough: 0.35, grit: 0 };
  private wanderIn = 0; private flutter = 0; private flutterPhase = 0;
  private gritLeft = 0; private gritIn = 0; private gritAmp = 0;
  private readonly dc = new Biquad(); private readonly body1 = new Biquad(); private readonly body2 = new Biquad(); private readonly lp = new Biquad();
  private readonly scrubBp = new Biquad(); private readonly scrubHi = new Biquad(); private readonly scrubLp = new Biquad();
  private readonly rollBp = new Biquad(); private readonly rollLp = new Biquad(); private readonly gritBp = new Biquad();
  private readonly roll: number; private readonly gritOn: boolean;

  constructor(private readonly sampleRate: number, seed: number, options: TyreSynthOptions = {}) {
    const sr = sampleRate, pitch = options.pitch ?? 1;
    this.rng = new Rng(seed);
    this.roll = options.roll ?? 1; this.gritOn = options.grit ?? true;
    this.blocks = BLOCKS.map(([hz, share], i) => ({ hz: hz * pitch, share, zeta: 0.02 + 0.006 * (i % 3), x: 0, v: 0, stuck: true,
      n1: 0, n2: 0, wander: 0, wanderTarget: 0, last: 0, k: 0, c2: 0 }));
    // The block motion radiates through the tyre body: high-pass below the tone, peaks at the first two harmonics.
    this.dc.highpass(640 * pitch, 0.8, sr); this.body1.peaking(1080 * pitch, 1.2, 4, sr); this.body2.peaking(2200 * pitch, 1.3, 9, sr);
    this.lp.lowpass(5200, 0.6, sr);
    this.scrubBp.bandpass(1300 * pitch, 0.8, sr); this.scrubHi.bandpass(4200, 0.8, sr); this.scrubLp.lowpass(3800, 0.6, sr);
    this.rollBp.bandpass(950, 0.6, sr); this.rollLp.lowpass(260, 0.7, sr); this.gritBp.bandpass(3200, 1.2, sr);
  }

  frame(f: TyreFrame) { this.target = f; }

  /** Nothing to render and nothing ringing: the target and the glided state are both silent. */
  get idle() {
    const t = this.target, c = this.cur;
    return t.slide < 0.02 && c.slide < 0.02 && (this.roll <= 0 || (t.speed < 0.3 && c.speed < 0.3));
  }

  render(out: Float32Array, gain = 1) {
    const n = out.length, sr = this.sampleRate, c = this.cur, t = this.target;
    const glide = 1 - Math.exp(-n / (0.03 * sr));
    c.slide += (t.slide - c.slide) * glide; c.load += (t.load - c.load) * glide; c.speed += (t.speed - c.speed) * glide;
    c.squeal += (t.squeal - c.squeal) * glide; c.rough += (t.rough - c.rough) * glide; c.grit += (t.grit - c.grit) * glide;
    if (this.idle) { for (const b of this.blocks) { b.x = 0; b.v = 0; b.stuck = true; b.last = 0; } return; }
    const V = Math.max(0, c.slide), load = Math.max(0.05, c.load), dt = 1 / (sr * OVERSAMPLE);
    // The resonances wander by a few percent (temperature, contact shape): new targets about three times a second.
    this.wanderIn -= n;
    if (this.wanderIn <= 0) { this.wanderIn = sr * this.rng.range(0.2, 0.5); for (const b of this.blocks) b.wanderTarget = this.rng.bi() * 0.035; }
    const wanderK = 1 - Math.exp(-n / (0.25 * sr));
    // Rubber keeps losing grip as it slides faster (heating), so the tone survives a long slide.
    const dMu = 0.42 * c.squeal, muD = MU_STATIC - dMu;
    const sliding = V > 0.02, roughNow = c.rough * (1 + V / 14);
    const roughK = Math.exp(-2 * Math.PI * 420 / sr), roughK2 = Math.exp(-2 * Math.PI * 35 / sr);
    for (const b of this.blocks) {
      b.wander += (b.wanderTarget - b.wander) * wanderK;
      // More load lengthens the contact patch: the block resonance falls a little.
      const w = 2 * Math.PI * b.hz * (1 + b.wander) * (1 - 0.1 * (load - 1));
      b.k = w * w; b.c2 = 2 * b.zeta * w;
      if (!sliding) { b.x = 0; b.v = 0; b.stuck = true; b.last = 0; }
    }
    // Loudness grows slower than sliding speed.
    const toneGain = 0.019 / (1 + 0.06 * V), derivative = sr / (2 * Math.PI * 1000);
    const scrubBase = 0.055 * Math.min(V, 25) ** 0.8 * Math.sqrt(load) * (0.45 + 0.9 * c.rough), judderDepth = 0.25 * Math.min(1, V / 3);
    const rollGain = this.roll * 0.03 * (Math.min(c.speed, 60) / 10) ** 1.4 * (0.5 + c.rough);
    const gritGain = this.gritOn && c.grit > 0.01 && (V > 0.05 || c.speed > 0.5) ? c.grit * 0.5 * Math.min(1, 0.15 + V / 6 + c.speed / 30) : 0;
    const gritRate = c.grit * (40 + 30 * V + 6 * c.speed);
    for (let i = 0; i < n; i++) {
      let tone = 0;
      if (sliding) {
        for (let bi = 0; bi < this.blocks.length; bi++) {
          const b = this.blocks[bi];
          // Road roughness: band-limited noise on the normal force (fast texture and slow patches).
          const white = this.rng.bi();
          b.n1 = roughK * b.n1 + (1 - roughK) * white; b.n2 = roughK2 * b.n2 + (1 - roughK2) * white;
          const N = N0 * load * b.share * 2.2 * Math.max(0.05, 1 + roughNow * (1.3 * b.n1 + 7 * b.n2));
          const Vb = V * (1 + 1.5 * c.rough * b.n2), limit = MU_STATIC * N;
          let sum = 0;
          for (let s = 0; s < OVERSAMPLE; s++) {
            if (b.stuck) {
              b.v = Vb; b.x += Vb * dt;
              if (b.k * b.x + b.c2 * Vb > limit) b.stuck = false;
            } else {
              const rel = Vb - b.v, mu = muD + dMu * Math.exp(-Math.abs(rel) / STRIBECK);
              const v = b.v + (-b.k * b.x - b.c2 * b.v + (rel >= 0 ? mu : -mu) * N) * dt;
              if (rel > 0 && v >= Vb && Math.abs(b.k * b.x + b.c2 * Vb) <= limit) { b.v = Vb; b.stuck = true; } else b.v = v;
              b.x += b.v * dt;
            }
            sum += b.v - Vb;
          }
          // A vibrating block radiates with its acceleration: the derivative lifts the harmonics to the measured
          // levels (2nd about -8 dB, 3rd about -19 dB re the fundamental band).
          const velocity = sum / OVERSAMPLE;
          tone += 0.3 * velocity + 0.7 * (velocity - b.last) * derivative;
          b.last = velocity;
        }
      }
      let y = this.lp.process(this.body2.process(this.body1.process(this.dc.process(tone) * toneGain)));
      const noise = this.rng.bi();
      if (scrubBase > 1e-6) {
        // Scrub: rubber sliding without a tone; it flutters at a few tens of hertz.
        this.flutterPhase += (38 + 14 * this.flutter) / sr; if (this.flutterPhase > 1) { this.flutterPhase -= 1; this.flutter = this.rng.bi(); }
        y += scrubBase * (1 + judderDepth * Math.sin(2 * Math.PI * this.flutterPhase))
          * this.scrubLp.process(this.scrubBp.process(noise) + 0.2 * this.scrubHi.process(noise));
      }
      if (rollGain > 1e-6) y += rollGain * (this.rollBp.process(noise) + 2.5 * this.rollLp.process(noise));
      if (gritGain > 0) {
        // Loose gravel: short random ticks instead of a tone.
        if (--this.gritIn <= 0) { this.gritIn = sr / gritRate * this.rng.range(0.2, 1.8); this.gritLeft = Math.round(sr * this.rng.range(0.0008, 0.0028)); this.gritAmp = this.rng.range(0.3, 1); }
        if (this.gritLeft > 0) { y += gritGain * this.gritAmp * this.gritBp.process(this.rng.bi()); this.gritLeft--; }
      }
      out[i] += y * gain;
    }
  }
}
