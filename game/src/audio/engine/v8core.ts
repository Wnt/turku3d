import { Biquad, OnePole, Rng, clamp, dbToGain } from './dsp';

/** Runtime V8 synthesis for the trophy truck (SPEC §4.3): a naturally aspirated crossplane V8 with open long-tube
 *  headers. Pure DSP, shared by the AudioWorklet (v8.worklet.ts) and the Node tests; no recorded audio.
 *
 *  The waveform is one engine cycle (two crank turns) held in cycle-locked additive wavetables: partial k sits at
 *  k·rpm/120, so k = 8 is the V8 firing frequency (62 Hz at the verified 930 rpm idle) and the odd half orders are the
 *  crossplane's uneven per-bank pulses. Three
 *  tables (on-load, overrun, idle) crossfade by load and rpm. Per cycle the length jitters and each of the eight
 *  firing slots gets its own amplitude (the lumpy cam), and the exhaust's fixed resonances sit after the oscillator,
 *  so the timbre does not resample with rpm. Noise layers add the open-exhaust rasp (pulsed by the firing train),
 *  the intake, overrun crackle and the ignition-cut limiter. The band balance is fitted to the verified references
 *  only (ref/REFERENCE.md §5, SPEC C5: the V2 shop idle and the V1 film pull); no reference audio enters the code. */

export const TABLE_SIZE = 2048;
export const MAX_PARTIALS = 32;
/** The rpm at which the on-load table holds exactly (the verified V1 film pull's half-order reading, ref §5). */
export const REFERENCE_RPM = 6060;
/** The verified idle (V2 shop, ref §5): 930 rpm, firing line 62 Hz. */
export const IDLE_RPM = 930;

/** On-load partial levels (dB re the strongest, k = 1..32), a design table fitted to the verified V1 film pull:
 *  the strongest partials sit at k5 and k8 (the firing order), then k4 and k7, as in the pull's 252/404/202/352 Hz.
 *  The crossplane's uneven bank pulses keep the odd half orders (k1, k3, k5, k7) alive. Above k16 the ladder falls
 *  toward the 1 kHz knee. */
export const ON_LOAD_DB: readonly number[] = [-6, -9, -6, -2, 0, -6, -3, 0, -9, -7, -11, -8, -13, -13, -14, -9,
  -15, -16, -17, -18, -19, -20, -21, -22, ...Array.from({ length: 8 }, () => -24)];
/** Overrun: k3/k5 −6, k8 −8, k1-2 +3 (burble), the rest −3, relative to the on-load table. */
export const OVERRUN_DB: readonly number[] = ON_LOAD_DB.map((db, i) => db + (i === 2 || i === 4 ? -6 : i === 7 ? -8 : i < 2 ? 3 : -3));
/** Idle: the firing line (k8, 62 Hz at 930 rpm) dominates with the lumpy cam's low half orders under it; k5-7 −3.
 *  The high orders sit at 60-250 Hz at idle, so k13-32 hold −20 dB (the 220 Hz exhaust resonance lifts those near it). */
export const IDLE_DB: readonly number[] = ON_LOAD_DB.map((db, i) => (i >= 12 ? -20 : db + (i === 7 ? 6 : i >= 4 && i <= 6 ? -3 : 0)));

export interface V8Params {
  rpm: number;
  /** −1 overrun .. +1 full load (EngineDrive.load). */
  load: number;
  throttle: number;
  limiter: boolean;
  shifting: boolean;
  /** False shuts the engine down; a voice created cold runs the starter when this turns true. */
  running: boolean;
  /** In-cockpit mix (more intake and low orders) instead of the exterior mix. */
  cockpit: boolean;
  /** 32 for near voices, 16 for distant remote voices. */
  partials: 16 | 32;
}

export interface V8Options { partials?: 16 | 32; running?: boolean }

interface Tables { on: Float32Array; off: Float32Array; idle: Float32Array; rasp: Float32Array }
interface Burst { left: number; length: number; amp: number; bp: Biquad; popHz: number; popPhase: number }

export interface V8Voice {
  readonly seed: number;
  readonly sampleRate: number;
  tables: Partial<Record<16 | 32, Tables>>;
  rng: Rng;
  phases: Float32Array;
  phase: number; jitter: number;
  slots: Float32Array; cyl: Float32Array;
  rpm: number; gain: number; cut: number; shiftGain: number;
  wOn: number; wOff: number; wIdle: number; wRasp: number;
  eq180: Biquad; eq225: Biquad; eq420: Biquad; lp: Biquad; hp: Biquad; shelf: Biquad;
  raspHp: Biquad; raspLp: Biquad; raspTilt: OnePole; raspPeak: Biquad;
  rumble: Biquad; body: Biquad;
  intake: Biquad; hiss: Biquad; bark: Biquad;
  starterLp: OnePole;
  bursts: Burst[];
  crackleLeft: number; crackleLength: number; crackleRate: number; nextBurst: number;
  limiterTimer: number; limiterPeriod: number;
  time: number;
  throttlePeak: number; throttlePeakAt: number;
  mode: 'off' | 'crank' | 'catch' | 'run' | 'stopping';
  modeTime: number;
  starterPhase: number;
  /** Diagnostics for tests and the snapshot. */
  last: { rpm: number; gain: number; crackle: boolean; cutting: boolean; mode: string };
}

const TWO_PI = Math.PI * 2;
/** Bank of each firing slot in firing order 1-8-4-3-6-5-7-2 (0 left: 1,3,5,7; 1 right: 2,4,6,8), plus a wrap entry. */
const BANK = [0, 1, 1, 0, 1, 0, 0, 1, 0];
const EXP_TABLE = Float32Array.from({ length: 257 }, (_, i) => Math.exp(-8 * i / 256));
const COS_TABLE = Float32Array.from({ length: 257 }, (_, i) => (1 - Math.cos(Math.PI * i / 256)) / 2);
/** Output level at full load, 6,060 rpm, before the bus gain. */
const FULL_SCALE = 0.32;
/** Noise-layer levels relative to the harmonic voice, fitted to the verified band shares (ref §5, SPEC C5):
 *  `idle` applies below 1,000 rpm, `load` at full load, blended by the table weights. */
export const MIX = {
  rumble: { idle: 2.5, load: 30 },
  rasp: { idle: 2.3, load: 4.3 },
  hiss: { idle: 1.6, load: 1.0 },
  pulse: 8, bark: 10, body: 0.4,
};

function buildTables(voice: V8Voice, partials: 16 | 32): Tables {
  const sr = voice.sampleRate, fRef = REFERENCE_RPM / 120;
  // Pre-compensate the fixed exhaust resonances at the reference rpm, so the output matches the design table
  // there and the formants stay put in Hz at every other rpm.
  const eq1 = new Biquad().peaking(180, 1.2, 3, sr), eq2 = new Biquad().peaking(420, 2, 2, sr), eq3 = new Biquad().peaking(220, 4, 9, sr);
  const comp = (k: number) => 1 / (eq1.gainAt(k * fRef, sr) * eq2.gainAt(k * fRef, sr) * eq3.gainAt(k * fRef, sr));
  // k >= 10 sit 2 dB under the table; the rasp table adds them back above 5,000 rpm, so at the reference rpm the
  // output matches ON_LOAD_DB.
  const make = (levels: readonly number[], from = 1) => {
    const t = new Float32Array(TABLE_SIZE + 1);
    for (let k = from; k <= partials; k++) {
      const a = dbToGain(levels[k - 1] - (k >= 10 ? 2 : 0)) * comp(k), ph = voice.phases[k - 1];
      for (let n = 0; n < TABLE_SIZE; n++) t[n] += a * Math.sin(TWO_PI * k * n / TABLE_SIZE + ph);
    }
    return t;
  };
  const on = make(ON_LOAD_DB), off = make(OVERRUN_DB), idle = make(IDLE_DB), rasp = make(ON_LOAD_DB, 10);
  let rms = 0;
  for (let n = 0; n < TABLE_SIZE; n++) rms += on[n] * on[n];
  const scale = 1 / Math.sqrt(rms / TABLE_SIZE);
  for (const t of [on, off, idle, rasp]) { for (let n = 0; n < TABLE_SIZE; n++) t[n] *= scale; t[TABLE_SIZE] = t[0]; }
  return { on, off, idle, rasp };
}

export function createV8(seed: number, sampleRate = 48000, options: V8Options = {}): V8Voice {
  const rng = new Rng(seed), sr = sampleRate;
  const voice: V8Voice = {
    seed, sampleRate: sr, tables: {}, rng,
    phases: Float32Array.from({ length: MAX_PARTIALS }, () => rng.next() * TWO_PI),
    phase: 0, jitter: 1,
    slots: new Float32Array(9).fill(1),
    // Fixed per-cylinder pulse strength in firing order (1-8-4-3-6-5-7-2): runner lengths and cam lobes differ.
    cyl: Float32Array.from({ length: 8 }, () => 1 + rng.bi() * 0.03),
    rpm: options.running === false ? 0 : IDLE_RPM, gain: 0, cut: 1, shiftGain: 1,
    wOn: 0, wOff: 0, wIdle: 1, wRasp: 0,
    eq180: new Biquad().peaking(180, 1.2, 3, sr), eq225: new Biquad().peaking(220, 4, 9, sr), eq420: new Biquad().peaking(420, 2, 2, sr),
    lp: new Biquad().lowpass(1000, Math.SQRT1_2, sr), hp: new Biquad().highpass(24, Math.SQRT1_2, sr),
    shelf: new Biquad().lowshelf(250, 3, sr),
    raspHp: new Biquad().highpass(800, Math.SQRT1_2, sr), raspLp: new Biquad().lowpass(4000, Math.SQRT1_2, sr),
    raspTilt: new OnePole().lowpass(1300, sr), raspPeak: new Biquad().peaking(1700, 0.8, 6, sr),
    // One biquad per noise layer (the worklet runs up to four voices per quantum).
    rumble: new Biquad().lowpass(90, 0.9, sr), body: new Biquad().lowpass(900, Math.SQRT1_2, sr),
    bark: new Biquad().bandpass(650, 0.6, sr), hiss: new Biquad().highpass(3200, 1, sr),
    intake: new Biquad().bandpass(400, 0.7, sr),
    starterLp: new OnePole().lowpass(2500, sr),
    bursts: Array.from({ length: 4 }, () => ({ left: 0, length: 1, amp: 0, bp: new Biquad(), popHz: 120, popPhase: 0 })),
    crackleLeft: 0, crackleLength: 1, crackleRate: 12, nextBurst: 0,
    limiterTimer: 0, limiterPeriod: 0.07,
    time: 0,
    throttlePeak: 0, throttlePeakAt: 0,
    mode: options.running === false ? 'off' : 'run', modeTime: 0, starterPhase: 0,
    last: { rpm: 0, gain: 0, crackle: false, cutting: false, mode: 'run' },
  };
  voice.tables[options.partials ?? 32] = buildTables(voice, options.partials ?? 32);
  newCycle(voice, voice.rpm || IDLE_RPM);
  return voice;
}

/** Draw the next cycle's length jitter and per-slot amplitudes (larger at idle, SPEC §4.3). */
function newCycle(v: V8Voice, rpm: number) {
  const x = clamp((rpm - IDLE_RPM) / (6000 - IDLE_RPM));
  const jitter = 0.015 + (0.004 - 0.015) * x, vary = 0.08 + (0.02 - 0.08) * x;
  v.jitter = 1 + v.rng.bi() * jitter;
  v.slots[0] = v.slots[8];
  for (let j = 1; j < 9; j++) v.slots[j] = v.cyl[j & 7] * (1 + v.rng.bi() * vary);
}

function startBurst(v: V8Voice, amp: number) {
  const b = v.bursts.find(x => x.left <= 0) ?? v.bursts.reduce((a, x) => (x.left < a.left ? x : a));
  b.length = b.left = Math.round(v.rng.range(0.01, 0.04) * v.sampleRate);
  b.amp = amp * v.rng.range(0.4, 1);
  b.bp.bandpass(v.rng.range(800, 4000), 1.3, v.sampleRate); b.bp.reset();
  b.popHz = v.rng.range(90, 170); b.popPhase = 0;
}

/** Renders one block into `out` (overwrites). Parameters are block-rate; rpm and gains ramp per sample. */
export function render(v: V8Voice, p: V8Params, out: Float32Array, sampleRate: number = v.sampleRate) {
  const n = out.length, dt = n / sampleRate;
  v.time += dt;
  const throttle = clamp(p.throttle), load = clamp(p.load, -1, 1);

  // Engine state: starter, catch flare and shutdown override the drive rpm.
  if (!p.running && v.mode !== 'off' && v.mode !== 'stopping') { v.mode = 'stopping'; v.modeTime = 0; }
  if (p.running && (v.mode === 'off' || v.mode === 'stopping')) { v.mode = v.rpm > 300 ? 'run' : 'crank'; v.modeTime = 0; }
  v.modeTime += dt;
  if (v.mode === 'crank' && v.modeTime >= 0.7) { v.mode = 'catch'; v.modeTime = 0; }
  if (v.mode === 'catch' && v.modeTime >= 1.6) v.mode = 'run';
  if (v.mode === 'stopping' && v.rpm < 30) { v.mode = 'off'; v.rpm = 0; }
  if (v.mode === 'off') { out.fill(0); v.last = { rpm: 0, gain: 0, crackle: false, cutting: false, mode: 'off' }; return; }

  let target = clamp(p.rpm, 0, 7600);
  if (v.mode === 'crank') target = 210 + 25 * Math.sin(TWO_PI * 13 * v.modeTime);
  else if (v.mode === 'catch') {
    const flare = v.modeTime < 0.15 ? 210 + (1400 - 210) * v.modeTime / 0.15 : 1400;
    target = v.modeTime < 0.15 ? flare : target + (1400 - target) * Math.exp(-(v.modeTime - 0.15) / 0.45);
  } else if (v.mode === 'stopping') target = Math.max(0, v.rpm - 2200 * dt);

  // Lift-off: a throttle drop of more than 0.5 within 0.15 s above 3,500 rpm starts an overrun crackle episode.
  // The throttle peak is held for 0.15 s, so a drop within that window reads against it.
  if (throttle >= v.throttlePeak || v.time - v.throttlePeakAt > 0.15) { v.throttlePeak = throttle; v.throttlePeakAt = v.time; }
  if (v.throttlePeak - throttle > 0.5 && v.rpm > 3500 && v.crackleLeft <= 0) {
    v.crackleLength = v.crackleLeft = v.rng.range(0.3, 0.8);
    v.crackleRate = v.rng.range(8, 20); v.nextBurst = 0;
  }

  // Hard limiter: 60-80 ms ignition-cut gating with crackle, only while the drivetrain flags it (never below 7,000).
  let cutting = false;
  if (p.limiter && v.rpm >= 6950 && v.mode === 'run') {
    v.limiterTimer += dt;
    if (v.limiterTimer >= v.limiterPeriod) { v.limiterTimer = 0; v.limiterPeriod = v.rng.range(0.06, 0.08); startBurst(v, 0.5); }
    cutting = v.limiterTimer < v.limiterPeriod * 0.45;
    if (cutting) target *= 1 - 0.025 * v.limiterTimer / (v.limiterPeriod * 0.45);
  } else v.limiterTimer = 0;

  // Table weights: idle below 1,000-2,000 rpm, then on-load or overrun by load; the high-order rasp above 5,000.
  const rpmEnd = target, x = clamp((rpmEnd - 1000) / 1000);
  const shiftLoad = p.shifting ? load * 0.4 : load;
  const wIdle = 1 - x, wOn = x * (shiftLoad >= 0 ? 1 : 1 + shiftLoad), wOff = x * Math.max(0, -shiftLoad);
  const wRasp = x * clamp((rpmEnd - 5000) / 500) * (shiftLoad >= 0 ? 1 : 1 + shiftLoad) * (dbToGain(2) - 1);
  const rpmDb = 10 * clamp(Math.log(Math.max(rpmEnd, 1) / IDLE_RPM) / Math.log(6600 / IDLE_RPM));
  // Level: +10 dB from idle to 6,600 rpm, +3.5 dB at full load (the noise layers carry the rest of the on-load
  // loudness), −4 dB on hard overrun.
  let levelDb = -15.5 + rpmDb + 3.5 * Math.max(0, shiftLoad) - 4 * Math.max(0, -shiftLoad);
  if (v.mode === 'crank') levelDb = -30;
  const gainEnd = FULL_SCALE * dbToGain(levelDb) * (v.mode === 'stopping' ? clamp(v.rpm / 600) : 1);
  const tableSet = v.tables[p.partials] ??= buildTables(v, p.partials);
  const { on, off, idle, rasp } = tableSet;

  const lopeDepth = 0.27 * clamp((1400 - rpmEnd) / 300);
  // On overrun the open pipes keep some rasp and hiss (the V1 rev arcs never fall silent between blips).
  const pos = Math.max(0, shiftLoad), neg = Math.max(0, -shiftLoad);
  const mixAt = (m: { idle: number; load: number }) => m.idle + (m.load - m.idle) * x * (0.3 + 0.7 * pos + 0.3 * neg);
  const raspEnd = gainEnd * mixAt(MIX.rasp);
  // Valve-train clatter and the exhaust's air hiss over 3 kHz, pulsed like the rasp.
  const hissEnd = gainEnd * mixAt(MIX.hiss);
  // Below 3,000 rpm the half-order ladder ends under 1.6 kHz; each firing's valve-opening blast adds the sharp
  // pressure pulse (the idle's "blat") that carries the firing harmonics up into the kHz range.
  const pulseEnd = gainEnd * MIX.pulse * clamp(1 - (rpmEnd - 1200) / 2400);
  const barkEnd = MIX.bark * pos;
  const bodyEnd = gainEnd * MIX.body * (0.5 + 0.5 * Math.max(0, shiftLoad) + 20 * (1 - x));
  // Mechanical and exhaust-flow rumble under 100 Hz, rising with load.
  // The exhaust-flow rumble grows with the gas flow (rpm × load), so it stays low in slow part-load driving.
  const flow = clamp((rpmEnd - 1000) / 4000) * (0.3 + 0.7 * pos);
  const rumbleEnd = gainEnd * (MIX.rumble.idle + (MIX.rumble.load - MIX.rumble.idle) * flow);
  const intakeEnd = gainEnd * dbToGain(p.cockpit ? -8 : -16) * 4.5 * throttle * clamp(rpmEnd / 7000);
  const crackleAmp = FULL_SCALE * 0.55;
  const cutTarget = cutting ? 0.1 : 1, shiftTarget = p.shifting ? 0.62 : 1;
  const starter = v.mode === 'crank' ? FULL_SCALE * 0.12 : 0;

  const rpm0 = v.rpm, g0 = v.gain, on0 = v.wOn, off0 = v.wOff, idle0 = v.wIdle, rasp0 = v.wRasp;
  const useOn = on0 > 0 || wOn > 0, useOff = off0 > 0 || wOff > 0, useIdle = idle0 > 0 || wIdle > 0, useRasp = rasp0 > 0 || wRasp > 0;
  const useIntake = intakeEnd > 1e-7;
  const inv = 1 / n, cutK = 1 - Math.exp(-1 / (0.0015 * sampleRate)), shiftK = 1 - Math.exp(-1 / (0.015 * sampleRate));
  // The crackle episode spawns bursts at its rate, fading over the episode.
  let burstIn = v.nextBurst;
  for (let i = 0; i < n; i++) {
    const t = (i + 1) * inv;
    const rpm = rpm0 + (rpmEnd - rpm0) * t;
    v.phase += rpm / 120 * v.jitter / sampleRate;
    if (v.phase >= 1) { v.phase -= 1; newCycle(v, rpm); }
    const idx = v.phase * TABLE_SIZE, i0 = idx | 0, fr = idx - i0;
    const a = on0 + (wOn - on0) * t, b = off0 + (wOff - off0) * t, c = idle0 + (wIdle - idle0) * t,
      d = rasp0 + (wRasp - rasp0) * t;
    // Tables whose weight is zero over the whole block are skipped (most blocks use one or two).
    let osc = useOn ? a * (on[i0] + (on[i0 + 1] - on[i0]) * fr) : 0;
    if (useOff) osc += b * (off[i0] + (off[i0 + 1] - off[i0]) * fr);
    if (useIdle) osc += c * (idle[i0] + (idle[i0 + 1] - idle[i0]) * fr);
    if (useRasp) osc += d * (rasp[i0] + (rasp[i0 + 1] - rasp[i0]) * fr);
    const sp = v.phase * 8, j = sp | 0, sf = sp - j, si = (sf * 256) | 0;
    const cw = COS_TABLE[si];
    osc *= v.slots[j] * (1 - cw) + v.slots[j + 1] * cw;
    v.cut += (cutTarget - v.cut) * cutK;
    v.shiftGain += (shiftTarget - v.shiftGain) * shiftK;
    // Idle lope: the whole engine swells once per cycle (≈ 7.75 Hz at 930 rpm), the lumpy cam's 6-8 Hz.
    const lope = lopeDepth > 0 ? 1 + lopeDepth * Math.sin(TWO_PI * v.phase) : 1;
    const g = (g0 + (gainEnd - g0) * t) * v.cut * v.shiftGain * lope;
    const noise = v.rng.bi();
    let blast = 0;
    if (pulseEnd > 1e-6) {
      // A fast-decaying pressure pulse at the start of every firing slot, scaled by that slot's strength.
      // Crossplane banks: the firing order 1-8-4-3-6-5-7-2 alternates the banks unevenly (L R L L R L R R), and the
      // right bank's pulse arrives later and softer through its longer pipe. That unevenness is the V8 burble.
      const right = BANK[j], age = sf * 15 / Math.max(rpm, 100) - (right ? 0.0006 : 0); // s since the slot began
      if (age >= 0) blast = pulseEnd * v.slots[j] * (right ? 0.7 : 1) * Math.exp(-age / (right ? 0.0012 : 0.0009))
        * (0.85 + 0.45 * noise) * v.cut * v.shiftGain * lope;
    }
    let y = v.lp.process(v.eq420.process(v.eq225.process(v.eq180.process(osc * g + blast))));
    // On load the same pulses bark through the open pipes above the 1 kHz knee (V2 snow driving: firing harmonics
    // to 1.2 kHz and beyond at 1,400-1,700 rpm).
    if (barkEnd > 0) y += v.bark.process(blast) * barkEnd;

    // Open-exhaust rasp: band-passed noise pulsed by the firing train.
    const pulse = 0.3 + 0.7 * EXP_TABLE[si];
    // Rumble (< 100 Hz) and hiss (> 3 kHz) share one noise draw: their bands do not overlap.
    const noise2 = v.rng.bi();
    y += v.rumble.process(noise2) * rumbleEnd * v.cut;
    // Combustion and flow noise between the orders (150-900 Hz), pulsed like the rasp.
    y += v.body.process(v.rng.bi()) * bodyEnd * (0.4 + 0.6 * EXP_TABLE[si]) * v.cut;
    y += v.raspPeak.process(v.raspTilt.process(v.raspLp.process(v.raspHp.process(noise)))) * raspEnd * pulse * v.cut;
    y += v.hiss.process(noise2) * hissEnd * pulse * v.cut;
    if (useIntake) y += v.intake.process(v.rng.bi()) * intakeEnd;

    if (v.crackleLeft > 0) {
      burstIn -= 1;
      if (burstIn <= 0) {
        const fade = clamp(v.crackleLeft / v.crackleLength);
        startBurst(v, crackleAmp * fade * fade);
        burstIn = sampleRate / v.crackleRate * v.rng.range(0.4, 1.6);
      }
    }
    for (let k = 0; k < 4; k++) {
      const bu = v.bursts[k];
      if (bu.left <= 0) continue;
      const age = bu.length - bu.left, env = Math.min(1, age / (0.001 * sampleRate)) * Math.exp(-3 * age / bu.length);
      bu.popPhase += bu.popHz / sampleRate;
      y += (bu.bp.process(v.rng.bi()) * 2.2 + 0.5 * Math.sin(TWO_PI * bu.popPhase) * Math.exp(-age / (0.006 * sampleRate))) * bu.amp * env;
      bu.left--;
    }

    if (starter > 0) {
      // Starter motor: pinion whirr on the ring gear, pulsed by the compression strokes.
      v.starterPhase += (420 + 160 * clamp(v.modeTime / 0.7)) / sampleRate;
      const comp = 0.6 + 0.4 * Math.sin(TWO_PI * v.phase * 4);
      y += starter * comp * (Math.sin(TWO_PI * v.starterPhase) * 0.6 + Math.sin(TWO_PI * v.starterPhase * 2.03) * 0.25
        + v.starterLp.process(v.rng.bi()) * 0.5);
    }
    y = v.hp.process(y);
    // A soft knee above ≈ 0.8 keeps the firing-pulse peaks inside full scale (≈ 1 dB of compression at the peaks).
    y /= Math.sqrt(1 + y * y * 0.6);
    if (p.cockpit) y = v.shelf.process(y);
    out[i] = y;
  }
  if (v.crackleLeft > 0) v.crackleLeft -= dt;
  v.nextBurst = burstIn;
  v.rpm = rpmEnd; v.gain = gainEnd; v.wOn = wOn; v.wOff = wOff; v.wIdle = wIdle; v.wRasp = wRasp;
  v.last = { rpm: rpmEnd, gain: gainEnd, crackle: v.crackleLeft > 0, cutting, mode: v.mode };
}
