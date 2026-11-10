// Procedural music engine prototype for Turku3D. Plain JS without imports or exports: the Node renderer evaluates
// this file and the soundboard inlines it into an AudioWorklet (the same pattern as the game's truck engine voice).
// Original Turku3D code and compositions, CC0-1.0.
//
// What it adds over the current sequencer (game/src/audio/music/index.ts): real chords and written melodies,
// instrument voices (band-limited oscillators, unison, resonant filters with envelopes, FM electric piano and bells),
// drum synthesis, swing and humanised velocities, a tempo-synced ping-pong delay, reverb, side-chain ducking, song
// form with fills, and layers that follow a 0..1 intensity (the delivery deadline) including the tempo lift.

function mtof(note) { return 440 * Math.pow(2, (note - 69) / 12); }
function polyblep(t, dt) {
  if (t < dt) { t /= dt; return t + t - t * t - 1; }
  if (t > 1 - dt) { t = (t - 1) / dt; return t * t + t + t + 1; }
  return 0;
}

// ---------- instruments ----------
const PRESETS = {
  bass: { wave: 'saw', unison: 2, detune: 5, sub: 0.55, cutoff: 190, env: 3.4, fDecay: 0.16, q: 1.5, a: 0.003, d: 0.22, s: 0.62, r: 0.07, level: 0.36, bus: 'duck', rev: 0, del: 0 },
  subBass: { wave: 'sine', unison: 1, detune: 0, sub: 0, cutoff: 900, env: 0, fDecay: 0.2, q: 0.7, a: 0.006, d: 0.5, s: 0.7, r: 0.09, level: 0.5, bus: 'duck', rev: 0, del: 0, drive: 1.6 },
  pad: { wave: 'saw', unison: 5, detune: 13, spread: 0.9, sub: 0, cutoff: 750, env: 1.1, fDecay: 1.6, q: 0.7, a: 0.55, d: 1.5, s: 0.8, r: 1.3, level: 0.15, bus: 'duck', rev: 0.4, del: 0.05 },
  softPad: { wave: 'tri', unison: 3, detune: 9, spread: 0.8, sub: 0.2, cutoff: 1100, env: 0.6, fDecay: 2, q: 0.6, a: 1.2, d: 2, s: 0.85, r: 2.2, level: 0.2, bus: 'main', rev: 0.6, del: 0.1 },
  pluck: { wave: 'pulse', pw: 0.34, unison: 2, detune: 6, spread: 0.3, sub: 0, cutoff: 520, env: 3.6, fDecay: 0.11, q: 1.3, a: 0.002, d: 0.16, s: 0, r: 0.12, level: 0.26, bus: 'main', rev: 0.14, del: 0.42 },
  lead: { wave: 'saw', unison: 2, detune: 8, spread: 0.25, sub: 0, cutoff: 2100, env: 1.3, fDecay: 0.35, q: 1.1, a: 0.012, d: 0.3, s: 0.78, r: 0.22, level: 0.34, bus: 'main', rev: 0.28, del: 0.34,
    vibRate: 5.4, vibDepth: 0.0035, vibDelay: 0.22 },
  stab: { wave: 'saw', unison: 3, detune: 11, spread: 0.6, sub: 0, cutoff: 430, env: 3.0, fDecay: 0.24, q: 2.4, a: 0.002, d: 0.32, s: 0, r: 0.22, level: 0.32, bus: 'main', rev: 0.45, del: 0.8 },
  brass: { wave: 'saw', unison: 3, detune: 9, spread: 0.5, sub: 0, cutoff: 900, env: 2.3, fDecay: 0.14, q: 1.0, a: 0.004, d: 0.2, s: 0.35, r: 0.1, level: 0.34, bus: 'main', rev: 0.2, del: 0.18 },
  seq: { wave: 'pulse', pw: 0.22, unison: 1, detune: 0, sub: 0, cutoff: 700, env: 2.8, fDecay: 0.07, q: 2.6, a: 0.001, d: 0.09, s: 0, r: 0.05, level: 0.3, bus: 'main', rev: 0.1, del: 0.3 },
  epiano: { wave: 'fm', ratio: 1, index: 2.2, indexFloor: 0.25, indexDecay: 0.45, tine: 0.25, unison: 1, detune: 0, sub: 0, cutoff: 5200, env: 0, fDecay: 1, q: 0.7, a: 0.003, d: 2.2, s: 0, r: 0.35, level: 0.24,
    bus: 'main', rev: 0.38, del: 0.12, tremolo: 0.25 },
  bell: { wave: 'fm', ratio: 3.51, index: 1.6, indexFloor: 0.05, indexDecay: 0.9, tine: 0, unison: 1, detune: 0, sub: 0, cutoff: 9000, env: 0, fDecay: 1, q: 0.7, a: 0.002, d: 3.0, s: 0, r: 1.2, level: 0.2,
    bus: 'main', rev: 0.6, del: 0.35 },
};

class SynthVoice {
  constructor(sr) { this.sr = sr; this.active = false; this.phases = new Float64Array(7); this.ratios = new Float64Array(7); this.pans = new Float64Array(7); }
  start(preset, note, velocity, seconds, pan, random) {
    const p = this.p = preset, sr = this.sr;
    this.active = true; this.age = 0; this.hold = Math.max(1, Math.round(seconds * sr)); this.released = false;
    this.freq = mtof(note); this.velocity = velocity; this.pan = pan;
    this.count = p.unison;
    for (let i = 0; i < this.count; i++) {
      const offset = this.count === 1 ? 0 : (i / (this.count - 1)) * 2 - 1;
      this.ratios[i] = Math.pow(2, offset * p.detune / 1200);
      this.pans[i] = Math.max(-1, Math.min(1, pan + offset * (p.spread ?? 0)));
      this.phases[i] = random();
    }
    this.subPhase = 0; this.modPhase = 0; this.tinePhase = 0;
    this.amp = 0; this.stage = 0; this.fEnv = 1; this.ic1 = 0; this.ic2 = 0; this.g = 0; this.k = 1 / p.q; this.control = 0;
    this.attackStep = 1 / Math.max(1, p.a * sr); this.decayK = Math.exp(-1 / (p.d * sr)); this.releaseK = Math.exp(-1 / (p.r * sr));
    this.fDecayK = Math.exp(-16 / (p.fDecay * sr)); // applied every 16 samples
    // Keyboard tracking: higher notes open the filter a little.
    this.cutoffBase = p.cutoff * Math.pow(this.freq / 261.6, 0.35);
  }
  /** Adds one block into the bus buffers. `open` scales every cutoff (the engine's brightness control). */
  render(n, buses, open) {
    const p = this.p, sr = this.sr, main = p.bus === 'duck' ? buses.duck : buses.main, level = p.level * this.velocity;
    const norm = 1 / Math.sqrt(this.count), fm = p.wave === 'fm';
    for (let i = 0; i < n; i++) {
      // Envelopes.
      if (!this.released && this.age >= this.hold) { this.released = true; this.stage = 2; }
      if (this.stage === 0) { this.amp += this.attackStep; if (this.amp >= 1) { this.amp = 1; this.stage = 1; } }
      else if (this.stage === 1) this.amp = p.s + (this.amp - p.s) * this.decayK;
      else { this.amp *= this.releaseK; if (this.amp < 0.0004) { this.active = false; return; } }
      if (this.control-- <= 0) {
        this.control = 15; this.fEnv *= this.fDecayK;
        const cutoff = Math.min(sr * 0.45, this.cutoffBase * open * Math.pow(2, p.env * this.fEnv));
        this.g = Math.tan(Math.PI * cutoff / sr);
        const a1 = 1 / (1 + this.g * (this.g + this.k)); this.a1 = a1; this.a2 = this.g * a1; this.a3 = this.g * this.a2;
      }
      const t = this.age / sr;
      let vib = 1;
      if (p.vibDepth && t > p.vibDelay) vib = 1 + p.vibDepth * Math.min(1, (t - p.vibDelay) * 4) * Math.sin(2 * Math.PI * p.vibRate * t);
      let left = 0, right = 0;
      if (fm) {
        const dt = this.freq * vib / sr, index = p.indexFloor + (p.index - p.indexFloor) * Math.exp(-t / p.indexDecay);
        this.modPhase += dt * p.ratio; this.phases[0] += dt; this.tinePhase += dt * 14;
        if (this.modPhase > 1) this.modPhase -= 1; if (this.phases[0] > 1) this.phases[0] -= 1; if (this.tinePhase > 1) this.tinePhase -= 1;
        let y = Math.sin(2 * Math.PI * this.phases[0] + index * Math.sin(2 * Math.PI * this.modPhase));
        if (p.tine) y += p.tine * Math.exp(-t / 0.05) * Math.sin(2 * Math.PI * this.tinePhase);
        if (p.tremolo) { const trem = p.tremolo * Math.sin(2 * Math.PI * 4.6 * t); left = y * (1 + trem); right = y * (1 - trem); }
        else { left = y; right = y; }
        const gl = Math.cos((this.pan + 1) * Math.PI / 4) * 1.414, gr = Math.sin((this.pan + 1) * Math.PI / 4) * 1.414;
        left *= gl; right *= gr;
      } else {
        for (let o = 0; o < this.count; o++) {
          const dt = this.freq * this.ratios[o] * vib / sr;
          let phase = this.phases[o] + dt; if (phase >= 1) phase -= 1; this.phases[o] = phase;
          let y;
          if (p.wave === 'saw') y = 2 * phase - 1 - polyblep(phase, dt);
          else if (p.wave === 'pulse') { const w = p.pw ?? 0.5; let p2 = phase + 1 - w; if (p2 >= 1) p2 -= 1; y = (phase < w ? 1 : -1) + polyblep(phase, dt) - polyblep(p2, dt); }
          else if (p.wave === 'tri') y = 4 * Math.abs(phase - 0.5) - 1;
          else y = Math.sin(2 * Math.PI * phase);
          const angle = (this.pans[o] + 1) * Math.PI / 4;
          left += y * Math.cos(angle); right += y * Math.sin(angle);
        }
        left *= norm * 1.414; right *= norm * 1.414;
        if (p.sub) { this.subPhase += this.freq * 0.5 / sr; if (this.subPhase > 1) this.subPhase -= 1; const s = p.sub * Math.sin(2 * Math.PI * this.subPhase); left += s; right += s; }
      }
      // Low-pass (TPT state-variable) on the mono sum; the stereo difference passes around it (cheap, keeps width).
      const mid = (left + right) * 0.5, side = (left - right) * 0.5;
      const v3 = mid - this.ic2, v1 = this.a1 * this.ic1 + this.a2 * v3, v2 = this.ic2 + this.a2 * this.ic1 + this.a3 * v3;
      this.ic1 = 2 * v1 - this.ic1; this.ic2 = 2 * v2 - this.ic2;
      let low = v2;
      if (p.drive) low = Math.tanh(low * p.drive);
      const sideGain = Math.min(1, this.g * 3), gain = this.amp * this.amp * level;
      const outL = (low + side * sideGain) * gain, outR = (low - side * sideGain) * gain;
      main[0][i] += outL; main[1][i] += outR;
      if (p.rev) { buses.rev[0][i] += outL * p.rev; buses.rev[1][i] += outR * p.rev; }
      if (p.del) { buses.del[0][i] += outL * p.del; buses.del[1][i] += outR * p.del; }
      this.age++;
    }
  }
}

/** One-pole helpers and a biquad band-pass for the drum voices. */
class DrumKit {
  constructor(sr, random) {
    this.sr = sr; this.random = random;
    this.hits = [];
    this.hp = { a: Math.exp(-2 * Math.PI * 7000 / sr), x: 0, y: 0 };
  }
  trigger(name, velocity, pan = 0) { this.hits.push({ name, velocity, pan, age: 0, phase: 0, phase2: 0, lp: 0, hpx: 0, hpy: 0, bp1: 0, bp2: 0 }); if (this.hits.length > 24) this.hits.shift(); }
  render(n, buses) {
    const sr = this.sr;
    for (let h = this.hits.length - 1; h >= 0; h--) {
      const hit = this.hits[h], v = hit.velocity;
      const gl = Math.cos((hit.pan + 1) * Math.PI / 4) * 1.414, gr = Math.sin((hit.pan + 1) * Math.PI / 4) * 1.414;
      let done = false;
      for (let i = 0; i < n; i++) {
        const t = hit.age / sr, noise = this.random() * 2 - 1;
        let y = 0, rev = 0;
        switch (hit.name) {
          case 'kick': { // pitch sweep 150 -> 47 Hz, a click, soft saturation
            const f = 47 + 105 * Math.exp(-t / 0.028); hit.phase += f / sr;
            y = Math.tanh(1.8 * Math.sin(2 * Math.PI * hit.phase) * Math.exp(-t / 0.2)) * 0.95 + noise * Math.exp(-t / 0.003) * 0.25;
            done = t > 0.9; break; }
          case 'softkick': { const f = 43 + 60 * Math.exp(-t / 0.04); hit.phase += f / sr;
            y = Math.sin(2 * Math.PI * hit.phase) * Math.exp(-t / 0.26) * Math.min(1, t / 0.002) * 0.9; done = t > 1.1; break; }
          case 'snare': { // two body tones and band-limited noise with a gated tail
            hit.phase += 186 / sr; hit.phase2 += 331 / sr;
            const body = (Math.sin(2 * Math.PI * hit.phase) + 0.6 * Math.sin(2 * Math.PI * hit.phase2)) * Math.exp(-t / 0.07);
            hit.lp += (noise - hit.lp) * 0.55; const bright = noise - hit.lp * 0.7;
            const gate = t < 0.2 ? 0.45 + 0.55 * Math.exp(-t / 0.05) : 0.45 * Math.exp(-(t - 0.2) / 0.025);
            y = body * 0.55 + bright * gate * 0.5; rev = 0.3; done = t > 0.5; break; }
          case 'clap': { hit.lp += (noise - hit.lp) * 0.35; const band = noise - hit.lp;
            const burst = (t < 0.03 ? Math.abs(Math.sin(Math.PI * t / 0.01)) : 0) + Math.exp(-Math.max(0, t - 0.03) / 0.09);
            y = band * burst * 0.5; rev = 0.45; done = t > 0.6; break; }
          case 'rim': { hit.phase += 1720 / sr; hit.phase2 += 890 / sr;
            y = (Math.sin(2 * Math.PI * hit.phase) + 0.5 * Math.sin(2 * Math.PI * hit.phase2)) * Math.exp(-t / 0.012) * 0.5 + noise * Math.exp(-t / 0.002) * 0.3; rev = 0.5; done = t > 0.15; break; }
          case 'hat': case 'openhat': case 'shaker': case 'crash': { // high-passed noise; decay sets the instrument
            const hp = hit.name === 'shaker' ? 0.62 : 0.5; // one-pole high-pass coefficient: about 4.7 kHz and 7.6 kHz
            const y0 = hp * (hit.hpy + noise - hit.hpx); hit.hpx = noise; hit.hpy = y0;
            const y1 = hp * (hit.bp1 + y0 - hit.bp2); hit.bp2 = y0; hit.bp1 = y1;
            const decay = hit.name === 'hat' ? 0.028 : hit.name === 'shaker' ? 0.035 : hit.name === 'openhat' ? 0.19 : 0.9;
            const attack = hit.name === 'shaker' ? Math.min(1, t / 0.012) : 1;
            y = y1 * Math.exp(-t / decay) * attack * (hit.name === 'crash' ? 0.5 : 0.42); rev = hit.name === 'crash' ? 0.5 : 0.08;
            done = t > decay * 7; break; }
          case 'tom': case 'lowtom': { const base = hit.name === 'tom' ? 150 : 98; const f = base * (1 + 0.5 * Math.exp(-t / 0.04)); hit.phase += f / sr;
            y = Math.sin(2 * Math.PI * hit.phase) * Math.exp(-t / 0.16) * 0.7 + noise * Math.exp(-t / 0.004) * 0.15; rev = 0.3; done = t > 0.8; break; }
          case 'tick': { hit.phase += 2400 / sr; y = Math.sin(2 * Math.PI * hit.phase) * Math.exp(-t / 0.006) * 0.4; rev = 0.2; done = t > 0.08; break; }
          default: done = true;
        }
        y *= v * 0.55;
        buses.main[0][i] += y * gl; buses.main[1][i] += y * gr;
        if (rev) { buses.rev[0][i] += y * gl * rev; buses.rev[1][i] += y * gr * rev; }
        hit.age++;
        if (done) break;
      }
      if (done) this.hits.splice(h, 1);
    }
  }
}

/** Freeverb-style reverb: eight damped combs and four all-passes per channel. */
class Reverb {
  constructor(sr, size = 0.86, damp = 0.35) {
    const scale = sr / 44100, combs = [1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617], alls = [556, 441, 341, 225];
    this.feedback = size; this.damp = damp;
    this.channels = [0, 23].map(spread => ({
      combs: combs.map(len => ({ buf: new Float32Array(Math.round((len + spread) * scale)), i: 0, store: 0 })),
      alls: alls.map(len => ({ buf: new Float32Array(Math.round((len + spread) * scale)), i: 0 })),
    }));
    this.pre = [new Float32Array(Math.round(0.022 * sr)), new Float32Array(Math.round(0.022 * sr))]; this.preI = 0;
  }
  process(inL, inR, outL, outR, n, gain) {
    const ins = [inL, inR], outs = [outL, outR];
    for (let ch = 0; ch < 2; ch++) {
      const c = this.channels[ch], input = ins[ch], out = outs[ch], pre = this.pre[ch];
      let pi = this.preI;
      for (let i = 0; i < n; i++) {
        const x = pre[pi] * 0.02; pre[pi] = input[i] + ins[1 - ch][i] * 0.3; if (++pi >= pre.length) pi = 0;
        let y = 0;
        for (const comb of c.combs) {
          const o = comb.buf[comb.i]; comb.store = o * (1 - this.damp) + comb.store * this.damp;
          comb.buf[comb.i] = x + comb.store * this.feedback; if (++comb.i >= comb.buf.length) comb.i = 0; y += o;
        }
        for (const all of c.alls) { const b = all.buf[all.i], v = -y + b; all.buf[all.i] = y + b * 0.5; if (++all.i >= all.buf.length) all.i = 0; y = v; }
        out[i] += y * gain;
      }
      if (ch === 1) this.preI = pi;
    }
  }
}

/** Ping-pong delay with a darkening feedback path. */
class Delay {
  constructor(sr) { this.sr = sr; this.buf = [new Float32Array(sr * 2), new Float32Array(sr * 2)]; this.i = 0; this.lp = [0, 0]; this.hp = [0, 0]; this.samples = sr * 0.3; this.feedback = 0.42; }
  process(inL, inR, outL, outR, n, gain) {
    const size = this.buf[0].length, d = Math.max(64, Math.min(size - 1, Math.round(this.samples)));
    for (let i = 0; i < n; i++) {
      let r = this.i - d; if (r < 0) r += size;
      const l0 = this.buf[0][r], r0 = this.buf[1][r];
      this.lp[0] += (l0 - this.lp[0]) * 0.32; this.lp[1] += (r0 - this.lp[1]) * 0.32;
      this.hp[0] += (this.lp[0] - this.hp[0]) * 0.02; this.hp[1] += (this.lp[1] - this.hp[1]) * 0.02;
      const fl = this.lp[0] - this.hp[0], fr = this.lp[1] - this.hp[1];
      // Cross feedback: left feeds right and back.
      this.buf[0][this.i] = inL[i] + inR[i] * 0.5 + fr * this.feedback;
      this.buf[1][this.i] = fl * this.feedback + inR[i] * 0.5;
      outL[i] += fl * gain; outR[i] += fr * gain;
      if (++this.i >= size) this.i = 0;
    }
  }
}

// ---------- songs ----------
// A song returns its form and an `onStep` that plays 16th-note step `step` of bar `bar`. Chords are MIDI note lists.
const SONGS = {
  // Night-drive synthwave in A minor: i i VI VI III III VII v.
  'aura-neon': {
    name: 'Aura Neon', bpm: 108, swing: 0, delaySteps: 3, delayFeedback: 0.4, reverb: [0.86, 0.3], reverbGain: 0.5,
    form: [['intro', 4], ['a', 8], ['a2', 8], ['break', 4], ['b', 8], ['a3', 8], ['b2', 8], ['outro', 4]], loopFrom: 1,
    setup() {
      this.chords = [[57, 60, 64, 71], [57, 60, 64, 71], [53, 57, 60, 64], [53, 57, 60, 64], [55, 60, 62, 64], [55, 60, 62, 64], [55, 59, 62, 67], [52, 55, 59, 62]];
      this.roots = [33, 33, 29, 29, 36, 36, 31, 28];
      this.arp = [0, 1, 2, 3, 2, 1, 0, 1, 2, 3, 2, 1, 2, 3, 1, 2];
      const A = [
        [[0, 76, 6], [6, 74, 2], [8, 72, 4], [12, 69, 4]], [[0, 71, 4], [4, 72, 2], [6, 71, 2], [8, 69, 8]],
        [[0, 72, 6], [6, 74, 2], [8, 76, 4], [12, 77, 4]], [[0, 76, 4], [4, 74, 4], [8, 72, 8]],
        [[0, 79, 6], [6, 76, 2], [8, 72, 4], [12, 74, 4]], [[0, 76, 8], [8, 74, 4], [12, 72, 4]],
        [[0, 74, 6], [6, 71, 2], [8, 67, 4], [12, 71, 4]], [[0, 71, 4], [4, 76, 4], [8, 71, 8]],
      ];
      const B = [
        [[0, 81, 4], [4, 79, 2], [6, 76, 2], [8, 79, 4], [12, 76, 2], [14, 74, 2]], [[0, 76, 6], [6, 74, 2], [8, 72, 2], [10, 74, 2], [12, 76, 4]],
        [[0, 77, 4], [4, 76, 2], [6, 72, 2], [8, 77, 4], [12, 81, 4]], [[0, 79, 6], [6, 77, 2], [8, 76, 8]],
        [[0, 84, 4], [4, 83, 2], [6, 79, 2], [8, 76, 4], [12, 79, 4]], [[0, 76, 2], [2, 79, 2], [4, 84, 4], [8, 83, 4], [12, 79, 4]],
        [[0, 83, 6], [6, 79, 2], [8, 74, 4], [12, 79, 4]], [[0, 76, 4], [4, 79, 4], [8, 83, 6], [14, 81, 2]],
      ];
      this.melody = { a2: A, a3: A, b: B, b2: B };
    },
    onStep(e, section, bar, barInSection, step, last) {
      const c = bar % 8, chord = this.chords[c], root = this.roots[c], drums = section !== 'intro' && section !== 'break' && section !== 'outro';
      if (step === 0 && (section !== 'outro' || barInSection < 3)) for (const note of chord) e.note('pad', note, 0.8, 15.5, 0);
      if (step === 0 && barInSection === 0 && drums) e.drum('crash', 0.5, 0.2);
      // Arpeggio: filtered and quiet in the intro, full later.
      const arpLevel = section === 'intro' ? 0.35 + 0.1 * barInSection : section === 'break' ? 0.5 : section === 'outro' ? 0.5 - 0.1 * barInSection : 0.75;
      if (arpLevel > 0.05) e.note('pluck', chord[this.arp[step]] + 12, arpLevel * (step % 4 === 0 ? 1 : 0.72), 0.9, step % 2 ? 0.45 : -0.45);
      if (drums) {
        if (step % 4 === 0) e.drum('kick', 0.95);
        if (step === 4 || step === 12) e.drum('snare', 0.85);
        if (step % 2 === 0) e.drum('hat', step % 4 === 2 ? 0.5 : 0.3, 0.25);
        else if (section[0] === 'b' || e.intensity > 0.5) e.drum('hat', 0.18, 0.25);
        if (step === 14 && c % 2 === 1) e.drum('openhat', 0.35, 0.25);
        // Bass: octave-jumping eighths.
        if (step % 2 === 0) e.note('bass', root + (step % 8 === 4 || step === 14 ? 12 : 0) + 12, step % 4 === 0 ? 0.95 : 0.8, 1.6, 0);
        // Tom fill into the next phrase.
        if (last && c === 7 && step >= 8) { const fill = { 8: 'tom', 10: 'tom', 12: 'lowtom', 13: 'lowtom', 14: 'snare', 15: 'snare' }[step]; if (fill) e.drum(fill, 0.55 + 0.04 * (step - 8), step < 12 ? -0.4 : 0.4); }
      } else if (section === 'break' && step === 0) e.note('bass', root + 12, 0.7, 14, 0);
      const melody = this.melody[section];
      if (melody) for (const [at, note, length] of melody[c]) if (at === step) e.note('lead', note, 0.85, length * 0.92, 0.05);
      if (section === 'break' && barInSection === 3 && step >= 8) e.drum('snare', 0.25 + 0.07 * (step - 8)); // snare build
    },
  },
  // Delivery rush in E minor: layers and tempo follow the intensity.
  'ring-road': {
    name: 'Ring Road', bpm: 132, swing: 0.08, delaySteps: 3, delayFeedback: 0.35, reverb: [0.8, 0.4], reverbGain: 0.35, lift: 0.14,
    form: [['cruise', 8], ['rush', 8], ['rush2', 8], ['peak', 8], ['peak2', 8]], loopFrom: 0, adaptive: true,
    setup() {
      this.chordsA = [[52, 55, 59, 64], [52, 55, 59, 64], [48, 52, 55, 60], [50, 54, 57, 62]]; this.rootsA = [28, 28, 24, 26]; // Em Em C D
      this.chordsB = [[55, 59, 62, 67], [50, 54, 57, 62], [48, 52, 55, 60], [47, 51, 54, 59]]; this.rootsB = [31, 26, 24, 23]; // G D C B
      this.riff = [0, 0, 12, 0, 0, 10, 0, 12, 0, 0, 12, 0, 7, 0, 10, 12];
      this.seq = [0, 2, 1, 3, 2, 1, 3, 2, 0, 3, 1, 2, 3, 1, 2, 3];
    },
    onStep(e, section, bar, barInSection, step, last) {
      const x = e.intensity, phrase = Math.floor(bar / 4) % 4 === 3, chords = phrase ? this.chordsB : this.chordsA, roots = phrase ? this.rootsB : this.rootsA;
      const chord = chords[bar % 4], root = roots[bar % 4], fillBar = bar % 4 === 3;
      // Breakbeat: kick 1 and the "and" of 2, snare 2 and 4; a four-on-the-floor kick takes over at high intensity.
      if (x > 0.72 ? step % 4 === 0 : step === 0 || step === 10 || (step === 6 && bar % 2 === 1)) e.drum('kick', 0.95);
      if (step === 4 || step === 12) e.drum('snare', 0.8); else if (x > 0.45 && (step === 7 || step === 15) && fillBar) e.drum('snare', 0.35);
      if (x > 0.3 ? true : step % 2 === 0) e.drum('hat', step % 4 === 2 ? 0.48 : step % 2 ? 0.16 + 0.12 * x : 0.3, 0.3);
      if (x > 0.2 && step % 4 === 2) e.drum('shaker', 0.2 + 0.2 * x, -0.4);
      if (step === 0 && barInSection === 0) e.drum('crash', 0.45, -0.2);
      e.note('bass', root + 12 + (this.riff[step] === 12 ? 12 : 0) + (x > 0.5 && this.riff[step] && this.riff[step] !== 12 ? this.riff[step] : 0), step % 4 === 0 ? 0.78 : 0.6, 0.8, 0);
      if (step === 0) for (const note of chord) e.note('pad', note, 0.55, 15.5, 0);
      // Off-beat chord stabs.
      if (step === 6 || step === 14 || (x > 0.55 && step === 3)) for (const note of chord) e.note('brass', note + 12, 0.6 + 0.3 * x, 1.2, 0.2);
      // Sequence line from 0.3, octave doubling (the old "panic" stem) from 0.65.
      if (x > 0.3) e.note('seq', chord[this.seq[step]] + 12, 0.45 + 0.4 * x, 0.7, step % 2 ? 0.5 : -0.5);
      if (x > 0.65) e.note('seq', chord[this.seq[(step + 8) % 16]] + 24, 0.3 + 0.4 * (x - 0.65) / 0.35, 0.5, step % 2 ? -0.6 : 0.6);
      if (x > 0.6 && fillBar && step >= 12) e.drum(step < 14 ? 'tom' : 'lowtom', 0.5 + 0.1 * (step - 12), step < 14 ? -0.4 : 0.4);
      // The last stretch: a clock tick on every beat and a bell on the bar.
      if (x > 0.85) { if (step % 4 === 0) e.drum('tick', 0.5, 0.5); if (step === 0) e.note('bell', chord[0] + 36, 0.6, 6, -0.3); }
    },
  },
  // Dub techno by the river in D minor.
  'river-dub': {
    name: 'River Dub', bpm: 120, swing: 0.1, delaySteps: 3, delayFeedback: 0.58, reverb: [0.9, 0.45], reverbGain: 0.6,
    form: [['intro', 8], ['main', 16], ['break', 8], ['main2', 16], ['outro', 8]], loopFrom: 1,
    setup() {
      this.chords = [[50, 53, 57, 60, 64], [50, 53, 57, 60, 64], [50, 53, 55, 58, 62], [53, 57, 58, 62, 65], [52, 55, 57, 60, 64]]; // Dm9 Dm9 Gm7 Bbmaj7 Am7
      this.roots = [38, 38, 43, 34, 33];
      this.plan = [0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 4, 4];
      this.hits = [[3, 0.9], [10, 0.7], [22, 0.8], [27, 0.5]]; // steps within a two-bar cell
    },
    onStep(e, section, bar, barInSection, step, last) {
      const index = this.plan[bar % 16], chord = this.chords[index], root = this.roots[index], kick = section === 'main' || section === 'main2';
      const cell = (bar % 2) * 16 + step;
      for (const [at, velocity] of this.hits) if (at === cell) for (const note of chord) e.note('stab', note, velocity * (section === 'outro' ? 0.7 : 1), 1.4, (note % 3 - 1) * 0.3);
      if (kick && step % 4 === 0) e.drum('softkick', 0.95);
      if (section !== 'outro' && (step === 4 || step === 12) && (kick || section === 'break')) e.drum('rim', 0.4, 0.3);
      if (section !== 'intro' || barInSection >= 4) { if (step % 4 === 2) e.drum('hat', 0.75, -0.3); if (step % 2 === 1) e.drum('shaker', 0.32, 0.4); }
      if (kick && step === 10 && bar % 4 === 3) e.drum('openhat', 0.3, -0.3);
      if (kick && (step === 0 || step === 3 || step === 8 || step === 11)) e.note('subBass', root, step === 0 ? 0.95 : 0.75, step === 8 ? 2.5 : 1.6, 0);
      if (step === 0 && bar % 4 === 0) e.note('softPad', chord[0] - 12, 0.5, 60, 0);
      if (step === 0 && barInSection === 0 && section === 'main2') e.drum('crash', 0.35, 0.3);
      if (section !== 'intro' && cell === 30 && bar % 4 === 1) e.note('bell', chord[4] + 12, 0.5, 4, 0.5);
    },
  },
  // Menu theme in G major: electric piano arpeggios, a soft pad and a sparse bell melody. No drums.
  'harbour-glass': {
    name: 'Harbour Glass', bpm: 72, swing: 0, delaySteps: 6, delayFeedback: 0.45, reverb: [0.92, 0.25], reverbGain: 0.7,
    form: [['a', 8], ['b', 8], ['a2', 8], ['b2', 8], ['outro', 2]], loopFrom: 0,
    setup() {
      this.chords = [[55, 59, 62, 66, 69], [52, 55, 59, 62, 66], [48, 55, 59, 60, 64], [50, 57, 59, 64, 66]]; // Gmaj9 Em9 Cmaj7(add9) D6/9
      this.roots = [43, 40, 36, 38];
      this.pattern = [0, 2, 3, 4, 3, 2, 4, 3, 1, 2, 3, 4, 2, 3, 4, 2];
      this.melody = [[[0, 86, 6], [8, 83, 4], [12, 81, 4]], [[4, 79, 4], [8, 83, 8]], [[0, 88, 6], [8, 86, 4], [12, 83, 4]], [[2, 81, 6], [8, 78, 8]],
        [[0, 83, 4], [4, 86, 4], [8, 90, 8]], [[0, 88, 6], [8, 86, 4], [12, 83, 4]], [[0, 84, 8], [8, 83, 4], [12, 79, 4]], [[0, 81, 12]]];
    },
    onStep(e, section, bar, barInSection, step, last) {
      const index = Math.floor(bar / 2) % 4, chord = this.chords[index], root = this.roots[index];
      if (section === 'outro') { if (step === 0 && barInSection === 0) { for (const note of this.chords[0]) e.note('epiano', note, 0.6, 30, 0); e.note('softPad', 43, 0.5, 30, 0); } return; }
      if (step === 0 && bar % 2 === 0) { for (const note of chord.slice(0, 4)) e.note('softPad', note, 0.7, 31, 0); e.note('subBass', root - 12 < 31 ? root : root - 12, 0.26, 30, 0); }
      if (step % 2 === 0) e.note('epiano', chord[this.pattern[(step / 2 + (bar % 2) * 8) % 16]] + (step === 0 ? 0 : 12), step % 8 === 0 ? 0.8 : 0.55, 5, (step % 4 === 0 ? -0.3 : 0.3));
      if (section !== 'a') for (const [at, note, length] of this.melody[bar % 8]) if (at === step) e.note('bell', note - 12, 0.55, length, 0.15);
    },
  },
};

class MusicCore {
  constructor(sampleRate, songId, seed) {
    this.sr = sampleRate;
    this.rng = (seed >>> 0) || 0x2545f491;
    this.random = () => { let s = this.rng; s ^= s << 13; s >>>= 0; s ^= s >>> 17; s ^= s << 5; s >>>= 0; this.rng = s; return s / 4294967296; };
    this.voices = Array.from({ length: 56 }, () => new SynthVoice(sampleRate));
    this.kit = new DrumKit(sampleRate, this.random);
    this.delay = new Delay(sampleRate);
    this.intensity = 0; this.muffle = 0; this.volume = 0.8; this.loop = false; this.finished = false;
    this.block = 0; this.side = 0; this.master = { hp: [0, 0], hpx: [0, 0], lp: [0, 0] };
    this.solo = null; // instrument name or 'drums' for stem renders
    this.select(songId);
  }
  select(songId) {
    const song = this.song = Object.create(SONGS[songId]);
    song.setup();
    this.reverb = new Reverb(this.sr, song.reverb[0], song.reverb[1]);
    this.delay.feedback = song.delayFeedback;
    this.bar = 0; this.step = 0; this.sectionIndex = 0; this.barInSection = 0; this.until = 0; this.finished = false; this.tail = 0;
    for (const voice of this.voices) voice.active = false;
    this.kit.hits.length = 0;
  }
  note(instrument, note, velocity, steps, pan) {
    if (this.solo && this.solo !== instrument) return;
    const voice = this.voices.find(v => !v.active) ?? this.voices.reduce((a, b) => (a.age > b.age ? a : b));
    const human = 1 + (this.random() - 0.5) * 0.12;
    voice.start(PRESETS[instrument], note, velocity * human, steps * this.stepSeconds, pan, this.random);
  }
  drum(name, velocity, pan = 0) {
    if (this.solo && this.solo !== 'drums') return;
    this.kit.trigger(name, velocity * (1 + (this.random() - 0.5) * 0.14), pan);
    if (name === 'kick' || name === 'softkick') this.side = 1;
  }
  get tempo() { const x = this.intensity, lift = this.song.lift ?? 0; return this.song.bpm * (1 + lift * x * x * (3 - 2 * x)); }
  advance() {
    const song = this.song, form = song.form, [section, bars] = form[this.sectionIndex];
    this.stepSeconds = 60 / this.tempo / 4;
    this.delay.samples = this.stepSeconds * song.delaySteps * this.sr;
    song.onStep(this, section, this.bar, this.barInSection, this.step, this.barInSection === bars - 1);
    // Swing: the off 16ths come late.
    const swing = song.swing * this.stepSeconds;
    this.until += (this.stepSeconds + (this.step % 2 === 0 ? swing : -swing)) * this.sr;
    if (++this.step === 16) {
      this.step = 0; this.bar++;
      if (++this.barInSection === bars) {
        this.barInSection = 0;
        if (++this.sectionIndex === form.length) { if (this.loop) this.sectionIndex = song.loopFrom; else { this.finished = true; this.sectionIndex = form.length - 1; } }
      }
    }
  }
  process(left, right) {
    const n = left.length, sr = this.sr;
    if (!this.buses || this.buses.main[0].length !== n) {
      const pair = () => [new Float32Array(n), new Float32Array(n)];
      this.buses = { main: pair(), duck: pair(), rev: pair(), del: pair() };
    }
    const b = this.buses;
    for (const bus of [b.main, b.duck, b.rev, b.del]) { bus[0].fill(0); bus[1].fill(0); }
    // Sequencer: steps that start inside this block fire at its start (2.7 ms at 128 frames; offline renders use 64).
    this.until -= n;
    while (this.until <= 0 && !this.finished) this.advance();
    if (this.finished) this.tail += n;
    const open = Math.pow(2, 1.2 * this.intensity * (this.song.adaptive ? 1 : 0.4));
    for (const voice of this.voices) if (voice.active) voice.render(n, b, open);
    this.kit.render(n, b);
    // Side-chain: the kick pushes the pad and bass down for a moment.
    const release = Math.exp(-1 / (0.16 * sr));
    for (let i = 0; i < n; i++) {
      const gain = 1 - 0.62 * this.side * this.side; this.side *= release;
      b.main[0][i] += b.duck[0][i] * gain; b.main[1][i] += b.duck[1][i] * gain;
    }
    this.delay.process(b.del[0], b.del[1], b.main[0], b.main[1], n, 0.5);
    // The delay's echoes feed the reverb a little as well.
    this.reverb.process(b.rev[0], b.rev[1], b.main[0], b.main[1], n, this.song.reverbGain);
    // Master: 30 Hz high-pass, the game's "inside the car" muffle, soft clip.
    const hpK = Math.exp(-2 * Math.PI * 30 / sr), cutoff = 18000 * Math.pow(850 / 18000, this.muffle), lpK = 1 - Math.exp(-2 * Math.PI * cutoff / sr), m = this.master;
    const out = [left, right];
    for (let ch = 0; ch < 2; ch++) {
      const x = b.main[ch], y = out[ch];
      for (let i = 0; i < n; i++) {
        const h = hpK * (m.hp[ch] + x[i] - m.hpx[ch]); m.hpx[ch] = x[i]; m.hp[ch] = h;
        m.lp[ch] += (h - m.lp[ch]) * lpK;
        y[i] = Math.tanh(m.lp[ch] * this.volume) * 0.95;
      }
    }
  }
}
