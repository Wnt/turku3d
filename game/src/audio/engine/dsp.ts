/** Small allocation-free DSP blocks shared by the engine and chassis synthesis (no Web Audio dependency, so the
 *  same code runs in the AudioWorklet and in Node tests). Biquads follow the RBJ audio-EQ cookbook. */

export class Biquad {
  b0 = 1; b1 = 0; b2 = 0; a1 = 0; a2 = 0; z1 = 0; z2 = 0;

  private set(b0: number, b1: number, b2: number, a0: number, a1: number, a2: number) {
    this.b0 = b0 / a0; this.b1 = b1 / a0; this.b2 = b2 / a0; this.a1 = a1 / a0; this.a2 = a2 / a0;
    return this;
  }
  lowpass(f: number, q: number, sr: number) {
    const w = 2 * Math.PI * Math.min(f, sr * 0.45) / sr, c = Math.cos(w), a = Math.sin(w) / (2 * q);
    return this.set((1 - c) / 2, 1 - c, (1 - c) / 2, 1 + a, -2 * c, 1 - a);
  }
  highpass(f: number, q: number, sr: number) {
    const w = 2 * Math.PI * Math.min(f, sr * 0.45) / sr, c = Math.cos(w), a = Math.sin(w) / (2 * q);
    return this.set((1 + c) / 2, -(1 + c), (1 + c) / 2, 1 + a, -2 * c, 1 - a);
  }
  /** Constant 0 dB peak gain band-pass. */
  bandpass(f: number, q: number, sr: number) {
    const w = 2 * Math.PI * Math.min(f, sr * 0.45) / sr, c = Math.cos(w), a = Math.sin(w) / (2 * q);
    return this.set(a, 0, -a, 1 + a, -2 * c, 1 - a);
  }
  peaking(f: number, q: number, db: number, sr: number) {
    const A = 10 ** (db / 40), w = 2 * Math.PI * f / sr, c = Math.cos(w), a = Math.sin(w) / (2 * q);
    return this.set(1 + a * A, -2 * c, 1 - a * A, 1 + a / A, -2 * c, 1 - a / A);
  }
  lowshelf(f: number, db: number, sr: number) {
    const A = 10 ** (db / 40), w = 2 * Math.PI * f / sr, c = Math.cos(w), s = Math.sin(w), a = s / Math.SQRT2;
    const r = 2 * Math.sqrt(A) * a;
    return this.set(A * ((A + 1) - (A - 1) * c + r), 2 * A * ((A - 1) - (A + 1) * c), A * ((A + 1) - (A - 1) * c - r),
      (A + 1) + (A - 1) * c + r, -2 * ((A - 1) + (A + 1) * c), (A + 1) + (A - 1) * c - r);
  }
  /** Magnitude response at `f` Hz. */
  gainAt(f: number, sr: number) {
    const w = 2 * Math.PI * f / sr, cr = Math.cos(w), ci = -Math.sin(w), c2r = Math.cos(2 * w), c2i = -Math.sin(2 * w);
    const nr = this.b0 + this.b1 * cr + this.b2 * c2r, ni = this.b1 * ci + this.b2 * c2i;
    const dr = 1 + this.a1 * cr + this.a2 * c2r, di = this.a1 * ci + this.a2 * c2i;
    return Math.hypot(nr, ni) / Math.hypot(dr, di);
  }
  process(x: number) {
    const y = this.b0 * x + this.z1;
    this.z1 = this.b1 * x - this.a1 * y + this.z2;
    this.z2 = this.b2 * x - this.a2 * y;
    return y;
  }
  reset() { this.z1 = 0; this.z2 = 0; }
}

/** One-pole low-pass (6 dB/oct). */
export class OnePole {
  a = 0; y = 0;
  lowpass(f: number, sr: number) { this.a = Math.exp(-2 * Math.PI * f / sr); return this; }
  process(x: number) { this.y = x + this.a * (this.y - x); return this.y; }
}

/** xorshift32: deterministic per seed, uniform in [0, 1). */
export class Rng {
  private s: number;
  constructor(seed: number) { this.s = (Math.imul((seed | 0) ^ 0x9e3779b9, 2654435761) >>> 0) || 0x2545f491; }
  next() {
    let s = this.s; s ^= s << 13; s ^= s >>> 17; s ^= s << 5; this.s = s >>> 0;
    return this.s / 4294967296;
  }
  /** Uniform in [−1, 1). */
  bi() { return this.next() * 2 - 1; }
  range(lo: number, hi: number) { return lo + (hi - lo) * this.next(); }
}

export const clamp = (v: number, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, Number.isFinite(v) ? v : lo));
export const dbToGain = (db: number) => 10 ** (db / 20);
