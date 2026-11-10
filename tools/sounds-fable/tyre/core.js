// Stick-slip tyre squeal synthesiser core. Plain JS without imports or exports: the Node renderer evaluates this
// file, and the soundboard inlines it into an AudioWorklet. Original Turku3D code, CC0-1.0.
//
// Model: a handful of tread-block oscillators (mass on a spring, dragged by the road at the sliding speed) with
// Coulomb/Stribeck friction and explicit stick and slip states. While a block sticks it moves with the road; when the
// spring force exceeds static friction it snaps back (slip) and is caught again. The emitted sound is the block
// velocity. Pitch, loudness, onset and the chirp at the start of a slide all come out of the model:
//   - stick time = 2·Δμ·N / (ω²·V): faster sliding or less load -> shorter stick phase -> higher pitch, up to the
//     block's own resonance; more load -> lower, rougher tone;
//   - Δμ (static minus kinetic friction) is the surface: smooth dry asphalt or painted concrete squeals, wet asphalt
//     and gravel do not;
//   - road roughness modulates the normal force, which gives the tone its hoarse, unsteady edge.
// A band of scrub noise (rubber sliding without a tone) and a rolling-noise layer complete the tyre.
class TyreSquealCore {
  constructor(sampleRate, seed) {
    this.sr = sampleRate;
    this.os = 4;
    this.dt = 1 / (sampleRate * this.os);
    this.rng = (seed >>> 0) || 0x9e3779b9;
    // Control targets; `set` changes them and `process` glides towards them.
    this.target = { slide: 0, load: 1, speed: 0, squeal: 1, rough: 0.35, grit: 0, tone: 1, scrub: 1, roll: 1 };
    this.cur = Object.assign({}, this.target);
    // Tread-block resonances (Hz) of the four tyres; the spread makes a cluster instead of one whistle.
    const table = [
      [905, 0.55, -0.6], [1180, 0.30, -0.5], [1490, 0.22, -0.7],
      [840, 0.50, 0.6], [1105, 0.32, 0.5], [1730, 0.15, 0.7],
      [760, 0.45, -0.25], [980, 0.34, 0.25], [1320, 0.2, 0],
    ];
    this.blocks = table.map(([hz, share, pan], i) => ({
      hz, share, pan, x: 0, v: 0, stuck: true, zeta: 0.02 + 0.006 * (i % 3),
      n1: 0, n2: 0, wander: 0, wanderTarget: 0, hp: 0, lastOut: 0, level: 0,
    }));
    this.wanderIn = 0;
    // Output filters (state per channel): DC blocker, body peaks, scrub band, roll band.
    this.f = {
      dc: [this.onePoleHp(180), this.onePoleHp(180)],
      body1: [this.biquad('peak', 1050, 1.1, 5), this.biquad('peak', 1050, 1.1, 5)],
      body2: [this.biquad('peak', 2600, 1.4, 4), this.biquad('peak', 2600, 1.4, 4)],
      lp: [this.biquad('lowpass', 9000, 0.7, 0), this.biquad('lowpass', 9000, 0.7, 0)],
      scrubBp: [this.biquad('bandpass', 1300, 0.8, 0), this.biquad('bandpass', 1300, 0.8, 0)],
      scrubHi: [this.biquad('bandpass', 4200, 0.8, 0), this.biquad('bandpass', 4200, 0.8, 0)],
      rollBp: [this.biquad('bandpass', 950, 0.6, 0), this.biquad('bandpass', 950, 0.6, 0)],
      rollLp: [this.biquad('lowpass', 260, 0.7, 0), this.biquad('lowpass', 260, 0.7, 0)],
      gritBp: [this.biquad('bandpass', 3200, 1.2, 0), this.biquad('bandpass', 3200, 1.2, 0)],
    };
    this.grit = [0, 0]; this.gritIn = [0, 0]; this.gritAmp = [0, 0];
    this.flutter = 0; this.flutterPhase = 0;
    this.decim = [0, 0];
  }

  random() { // xorshift32, 0..1
    let s = this.rng; s ^= s << 13; s >>>= 0; s ^= s >>> 17; s ^= s << 5; s >>>= 0; this.rng = s; return s / 4294967296;
  }
  noise() { return this.random() * 2 - 1; }

  onePoleHp(hz) { return { a: Math.exp(-2 * Math.PI * hz / this.sr), x: 0, y: 0 }; }
  static hp(f, x) { const y = f.a * (f.y + x - f.x); f.x = x; f.y = y; return y; }

  biquad(type, hz, q, gainDb) {
    const w = 2 * Math.PI * hz / this.sr, cos = Math.cos(w), sin = Math.sin(w), alpha = sin / (2 * q);
    let b0, b1, b2, a0, a1, a2;
    if (type === 'lowpass') { b0 = (1 - cos) / 2; b1 = 1 - cos; b2 = b0; a0 = 1 + alpha; a1 = -2 * cos; a2 = 1 - alpha; }
    else if (type === 'bandpass') { b0 = alpha; b1 = 0; b2 = -alpha; a0 = 1 + alpha; a1 = -2 * cos; a2 = 1 - alpha; }
    else { const A = Math.pow(10, gainDb / 40); b0 = 1 + alpha * A; b1 = -2 * cos; b2 = 1 - alpha * A; a0 = 1 + alpha / A; a1 = -2 * cos; a2 = 1 - alpha / A; }
    return { b0: b0 / a0, b1: b1 / a0, b2: b2 / a0, a1: a1 / a0, a2: a2 / a0, z1: 0, z2: 0 };
  }
  static bq(f, x) { const y = f.b0 * x + f.z1; f.z1 = f.b1 * x - f.a1 * y + f.z2; f.z2 = f.b2 * x - f.a2 * y; return y; }

  /** slide: sliding speed of the contact patch (m/s); load: normal force relative to static (0..2.5);
   *  speed: vehicle speed (m/s, rolling noise); squeal: 0..1 surface squeal tendency (Δμ);
   *  rough: 0..1 road roughness; grit: 0..1 loose gravel; tone/scrub/roll: layer gains. */
  set(params) { for (const k in params) if (k in this.target && Number.isFinite(params[k])) this.target[k] = params[k]; }

  process(left, right) {
    const n = left.length, sr = this.sr, os = this.os, dt = this.dt, c = this.cur, t = this.target, f = this.f;
    const glide = 1 - Math.exp(-n / (0.03 * sr));
    for (const k in c) c[k] += (t[k] - c[k]) * glide;
    const V = Math.max(0, c.slide), load = Math.max(0.05, c.load);
    // Slow random wander of every block's resonance (temperature, contact shape): new targets about 3 times a second.
    this.wanderIn -= n;
    if (this.wanderIn <= 0) {
      this.wanderIn = sr * (0.2 + 0.3 * this.random());
      for (const b of this.blocks) b.wanderTarget = this.noise() * 0.035;
    }
    const wanderK = 1 - Math.exp(-n / (0.25 * sr));
    const muS = 1.0, dMu = 0.42 * c.squeal, muD = muS - dMu, vStribeck = 5.5; // rubber keeps losing grip as it slides faster (heating): the tone survives a long slide
    const N0 = 2.6e4; // normal force per unit block mass at static load (m/s²); sets where the pitch saturates
    const active = V > 0.02 && c.tone > 0.001;
    const roughK = Math.exp(-2 * Math.PI * 420 / sr), roughK2 = Math.exp(-2 * Math.PI * 35 / sr);
    for (const b of this.blocks) {
      b.wander += (b.wanderTarget - b.wander) * wanderK;
      // More load lengthens the contact patch: the block resonance falls a little.
      b.w = 2 * Math.PI * b.hz * (1 + b.wander) * (1 - 0.1 * (load - 1));
      b.k = b.w * b.w; b.c2 = 2 * b.zeta * b.w;
    }
    for (let i = 0; i < n; i++) {
      let l = 0, r = 0;
      if (active) {
        for (let bi = 0; bi < this.blocks.length; bi++) {
          const b = this.blocks[bi];
          // Road roughness: band-limited noise on the normal force (fast texture + slow patches).
          const white = this.noise();
          b.n1 = roughK * b.n1 + (1 - roughK) * white; b.n2 = roughK2 * b.n2 + (1 - roughK2) * white;
          const N = N0 * load * b.share * 2.2 * Math.max(0.05, 1 + c.rough * (2.2 * b.n1 + 7 * b.n2));
          const Vb = V * (1 + 1.5 * c.rough * b.n2);
          let acc = 0;
          for (let s = 0; s < os; s++) {
            if (b.stuck) {
              b.v = Vb; b.x += Vb * dt;
              if (b.k * b.x + b.c2 * Vb > muS * N) b.stuck = false;
            } else {
              const rel = Vb - b.v, mu = muD + dMu * Math.exp(-Math.abs(rel) / vStribeck);
              const a = -b.k * b.x - b.c2 * b.v + (rel >= 0 ? mu : -mu) * N;
              const v = b.v + a * dt;
              if (rel > 0 && v >= Vb && Math.abs(b.k * b.x + b.c2 * Vb) <= muS * N) { b.v = Vb; b.stuck = true; }
              else b.v = v;
              b.x += b.v * dt;
            }
            acc += b.v - Vb;
          }
          const out = acc / os;
          l += out * (1 - b.pan) * 0.5; r += out * (1 + b.pan) * 0.5;
        }
      } else for (const b of this.blocks) { b.x *= 0.999; b.v = 0; b.stuck = true; }
      // Tone: velocity pulses -> DC blocker -> tyre body peaks. Level compressed: loudness grows slower than sliding speed.
      const toneGain = c.tone * 0.03 / (1 + 0.06 * V);
      let tl = TyreSquealCore.hp(f.dc[0], l) * toneGain, tr = TyreSquealCore.hp(f.dc[1], r) * toneGain;
      tl = TyreSquealCore.bq(f.body2[0], TyreSquealCore.bq(f.body1[0], tl));
      tr = TyreSquealCore.bq(f.body2[1], TyreSquealCore.bq(f.body1[1], tr));
      // Scrub: rubber sliding without a tone, growing with sliding speed and load; flutters at a few tens of hertz.
      this.flutterPhase += (38 + 14 * this.flutter) / sr; if (this.flutterPhase > 1) { this.flutterPhase -= 1; this.flutter = this.noise(); }
      const judder = 1 + 0.25 * Math.sin(2 * Math.PI * this.flutterPhase) * Math.min(1, V / 3);
      const scrubGain = c.scrub * 0.055 * Math.pow(Math.min(V, 25), 0.8) * Math.sqrt(load) * (0.45 + 0.9 * c.rough) * judder;
      const rollGain = c.roll * 0.03 * Math.pow(Math.min(c.speed, 60) / 10, 1.4) * (0.5 + c.rough);
      for (let ch = 0; ch < 2; ch++) {
        const w = this.noise();
        let y = 0;
        if (scrubGain > 1e-6) y += scrubGain * (TyreSquealCore.bq(f.scrubBp[ch], w) + 0.3 * TyreSquealCore.bq(f.scrubHi[ch], w));
        if (rollGain > 1e-6) y += rollGain * (TyreSquealCore.bq(f.rollBp[ch], w) + 2.5 * TyreSquealCore.bq(f.rollLp[ch], w));
        // Loose gravel: short random ticks instead of a tone.
        if (c.grit > 0.01 && (V > 0.05 || c.speed > 0.5)) {
          if (--this.gritIn[ch] <= 0) {
            const rate = c.grit * (40 + 30 * V + 6 * c.speed);
            this.gritIn[ch] = sr / rate * (0.2 + 1.6 * this.random());
            this.grit[ch] = Math.round(sr * (0.0008 + 0.002 * this.random())); this.gritAmp[ch] = 0.3 + 0.7 * this.random();
          }
          if (this.grit[ch] > 0) { y += c.grit * 0.5 * this.gritAmp[ch] * TyreSquealCore.bq(f.gritBp[ch], this.noise()) * Math.min(1, 0.15 + V / 6 + c.speed / 30); this.grit[ch]--; }
        }
        if (ch === 0) tl += y; else tr += y;
      }
      left[i] = TyreSquealCore.bq(f.lp[0], tl); right[i] = TyreSquealCore.bq(f.lp[1], tr);
    }
  }
}
