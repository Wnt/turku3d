import { expect, test } from 'vitest';
import { GameAudio } from '../../src/audio';
import { GameplayAudio } from '../../src/audio/gameplay';
import { ChassisSynth, ChassisTracker, TROPHY_CHASSIS } from '../../src/audio/engine/chassis';
import { SILENT_TYRES, TYRE_KINDS, TyreSynth, TyreTracker, slideSpeed, type TyreFrame } from '../../src/audio/engine/tyre';
import { TYRE_VOICES, selectTyreVoices, tyreAudible } from '../../src/audio/engine/tyreVoices';
import type { AudioEvent } from '../../src/audio/mix';
import type { WheelState } from '../../src/physics/protocol';
import { MAT } from '../../src/shared/col/materials';

const SR = 48000;
const wheel = (lateral: number, material: number = MAT.road, suspension = 0.3, longitudinal = 0, contact = true): WheelState => ({
  pos: { x: 0, y: 0, z: 0 }, steer: 0, rotation: 0, contact, suspension,
  tyre: contact ? { point: { x: 0, y: -0.3, z: 0 }, normal: { x: 0, y: 1, z: 0 }, material, lateral, longitudinal } : undefined });
const four = (lateral: number, material: number = MAT.road) => [0, 1, 2, 3].map(() => wheel(lateral, material));

function render(frame: Partial<TyreFrame>, seconds = 1.5, seed = 7, options = {}) {
  const synth = new TyreSynth(SR, seed, options), out = new Float32Array(Math.round(seconds * SR)), block = new Float32Array(128);
  synth.frame({ ...SILENT_TYRES, ...frame });
  for (let i = 0; i < out.length; i += 128) { block.fill(0); synth.render(block); out.set(block.subarray(0, Math.min(128, out.length - i)), i); }
  return out.subarray(SR / 2); // after the 30 ms parameter glide has settled
}
/** Power spectrum by a plain DFT at `hz` steps of 10 Hz (Hann window over the whole take). */
function spectrum(x: Float32Array, lo: number, hi: number) {
  const n = Math.min(x.length, 24000), bins: { hz: number; power: number }[] = [];
  for (let hz = lo; hz <= hi; hz += 10) {
    let re = 0, im = 0; const w = 2 * Math.PI * hz / SR;
    for (let i = 0; i < n; i++) { const v = x[i] * (0.5 - 0.5 * Math.cos(2 * Math.PI * i / (n - 1))); re += v * Math.cos(w * i); im -= v * Math.sin(w * i); }
    bins.push({ hz, power: re * re + im * im });
  }
  return bins;
}
const band = (bins: { hz: number; power: number }[], lo: number, hi: number) => bins.filter(b => b.hz >= lo && b.hz < hi).reduce((s, b) => s + b.power, 0);
const rms = (x: Float32Array) => Math.sqrt(x.reduce((s, v) => s + v * v, 0) / x.length);
const db = (v: number) => 10 * Math.log10(v + 1e-30);

test('slide speed: silent below the grip limit, audible before tyre marks start, bounded and finite', () => {
  expect(slideSpeed(0.5, 0, 20)).toBe(0);
  expect(slideSpeed(2.2, 0, 20)).toBe(0); // 69 % of the 3.2 m/s mark limit at 20 m/s
  const atMarks = slideSpeed(3.2, 0, 20);
  expect(atMarks).toBeGreaterThan(2); expect(atMarks).toBeLessThan(3.2);
  expect(slideSpeed(8, 0, 20)).toBeCloseTo(8, 5);
  expect(slideSpeed(0, 6, 10)).toBeCloseTo(6, 5); // wheelspin or a locked wheel
  expect(slideSpeed(-5, 0, 10)).toBe(slideSpeed(5, 0, 10));
  expect(slideSpeed(NaN, 0, 10)).toBe(0); expect(slideSpeed(1e9, 0, 10)).toBe(30);
});

/** The frame after `ticks` physics ticks of the same wheels (settled contact and the onset hold have passed by 12). */
function after(tracker: TyreTracker, wheels: WheelState[], speed: number, ticks = 12) {
  let frame = SILENT_TYRES;
  for (let i = 0; i < ticks; i++) frame = tracker.step(1 / 60, wheels, speed);
  return frame;
}

test('tyre tracker: asphalt squeals, gravel only crunches, a compressed tyre carries more load, airborne is silent', () => {
  const asphalt = after(new TyreTracker('car'), four(5), 15);
  expect(asphalt.slide).toBeCloseTo(5, 5); expect(asphalt.squeal).toBe(1); expect(asphalt.grit).toBe(0); expect(asphalt.speed).toBe(15);
  const gravel = after(new TyreTracker('car'), four(5, MAT.gravel), 15);
  expect(gravel.slide).toBeCloseTo(5, 5); expect(gravel.squeal).toBe(0); expect(gravel.grit).toBe(1); expect(gravel.rough).toBeGreaterThan(asphalt.rough);
  // Two wheels on the gravel shoulder: the tyres on the road still squeal.
  const mixed = after(new TyreTracker('car'), [wheel(4), wheel(4), wheel(6, MAT.gravel), wheel(6, MAT.gravel)], 15);
  expect(mixed.squeal).toBe(1); expect(mixed.slide).toBeCloseTo(4, 5); expect(mixed.grit).toBe(0.5);
  // The outer tyre in a corner compresses 4 cm beyond its rest length (at 0.4 m/s, no jolt): its load rises.
  const tracker = new TyreTracker('car');
  after(tracker, four(0), 10, 30);
  let loaded = SILENT_TYRES;
  for (let i = 1; i <= 12; i++) loaded = tracker.step(1 / 60, [wheel(5, MAT.road, 0.3 - Math.min(0.04, i * 0.0067)), wheel(0), wheel(0), wheel(0)], 10);
  expect(loaded.slide).toBeCloseTo(5, 5); expect(loaded.load).toBeGreaterThan(1.4); expect(loaded.load).toBeLessThanOrEqual(2.3);
  expect(after(new TyreTracker('car'), [0, 1, 2, 3].map(() => wheel(9, MAT.road, 0.3, 0, false)), 20)).toBe(SILENT_TYRES);
  expect(after(new TyreTracker('scooter'), [wheel(4), wheel(4)], 8).slide).toBeCloseTo(4, 5);
  expect(after(new TyreTracker('car'), four(5, MAT.water), 10)).toBe(SILENT_TYRES);
});

test('tyre tracker: a slide sounds 50 ms after 80 ms of settled contact; landings, bumps and jolts never chirp', () => {
  // Settled 80 ms, then 50 ms of sliding: heard from the 8th tick at 60 Hz (the tyre marks need 260 ms).
  const tracker = new TyreTracker('car'), frames = Array.from({ length: 10 }, () => tracker.step(1 / 60, four(5), 15).slide);
  expect(frames.slice(0, 7).every(v => v === 0)).toBe(true); expect(frames[8]).toBeCloseTo(5, 5);
  // A gap in the slide restarts the 50 ms hold; a suspension jolt (faster than 1.5 m/s) restarts both.
  expect(tracker.step(1 / 60, four(0.5), 15).slide).toBe(0);
  expect(tracker.step(1 / 60, four(5), 15).slide).toBe(0);
  expect(after(tracker, four(5), 15, 3).slide).toBeCloseTo(5, 5);
  expect(tracker.step(1 / 60, [0, 1, 2, 3].map(() => wheel(5, MAT.road, 0.34)), 15).slide).toBe(0);
  // The physics' pattern on a bumpy straight (tyre-effects.test.ts): airborne a tick, a jolt, large slip for 4 ticks.
  for (const dt of [1 / 60, 1 / 30]) {
    const bumpy = new TyreTracker('car');
    for (let i = 0; i < 180; i++) {
      const phase = i % 12, w = wheel(phase < 4 ? 6 : 1.8, MAT.road, phase === 1 ? 0.4 : 0.3, phase < 4 ? 8 : 0.5, phase !== 0);
      expect(bumpy.step(dt, [w], 18).slide).toBe(0);
    }
  }
});

test('tyre synth: silent and free while idle, deterministic, finite at the extremes', () => {
  const synth = new TyreSynth(SR, 3), block = new Float32Array(128);
  expect(synth.idle).toBe(true); synth.render(block); expect(block.every(v => v === 0)).toBe(true);
  synth.frame({ ...SILENT_TYRES, slide: 5 }); expect(synth.idle).toBe(false);
  expect(Array.from(render({ slide: 5 }, 0.6, 11))).toEqual(Array.from(render({ slide: 5 }, 0.6, 11)));
  for (const frame of [{ slide: 30, load: 2.3, speed: 60 }, { slide: 0.03, load: 0.3 }, { slide: 12, squeal: 1.2, rough: 1, grit: 1, speed: 40 }])
    expect(render(frame, 0.7).every(Number.isFinite)).toBe(true);
  // Loudness grows with sliding speed, slower than linearly.
  const quiet = rms(render({ slide: 1 })), mid = rms(render({ slide: 4 })), loud = rms(render({ slide: 8 }));
  expect(mid).toBeGreaterThan(quiet * 1.5); expect(loud).toBeGreaterThan(mid); expect(loud).toBeLessThan(quiet * 8);
});

test('tyre synth matches the measured squeal: a tone cluster near 1 kHz with harmonics, little above 7 kHz', () => {
  // Targets measured on CC0 recordings (BigSoundBank 2368-2371), band levels re the 0.85-1.3 kHz band:
  // 1.8-2.5 kHz -7..-11 dB, 2.8-3.7 kHz -18..-21 dB, 7-10 kHz -30..-41 dB, 0.3-0.7 kHz below -30 dB.
  const bins = spectrum(render({ slide: 5, load: 1.2 }, 1.2), 300, 10000), fundamental = band(bins, 850, 1300);
  expect(db(band(bins, 1800, 2500) / fundamental)).toBeGreaterThan(-15); expect(db(band(bins, 1800, 2500) / fundamental)).toBeLessThan(-2);
  expect(db(band(bins, 2800, 3700) / fundamental)).toBeGreaterThan(-26); expect(db(band(bins, 2800, 3700) / fundamental)).toBeLessThan(-10);
  expect(db(band(bins, 7000, 10000) / fundamental)).toBeLessThan(-26);
  expect(db(band(bins, 300, 700) / fundamental)).toBeLessThan(-18);
});

/** Pitch by autocorrelation between 200 and 2000 Hz. */
function pitch(frame: Partial<TyreFrame>, options = {}) {
  const x = render(frame, 1.2, 7, { roll: 0, ...options }), n = 12000;
  let best = 0, lag = 1;
  for (let k = Math.floor(SR / 2000); k < SR / 200; k++) { let c = 0; for (let i = 0; i + k < n; i++) c += x[i] * x[i + k]; if (c > best) { best = c; lag = k; } }
  return SR / lag;
}

test('tyre synth: the pitch follows slide and load, a small tyre sings higher, loose surfaces do not squeal', () => {
  const tonality = (frame: Partial<TyreFrame>) => { const bins = spectrum(render(frame, 1.2, 7, { roll: 0 }), 700, 1500).map(b => b.power).sort((a, b) => a - b);
    return db(bins[bins.length - 1] / bins[bins.length >> 1]); };
  // The stick phase shortens as the slide speeds up (a stopping skid falls in pitch); more load lowers the tone.
  expect(pitch({ slide: 6 })).toBeGreaterThan(850); expect(pitch({ slide: 6 })).toBeLessThan(1300);
  expect(pitch({ slide: 6 })).toBeGreaterThan(pitch({ slide: 2 }) + 30);
  expect(pitch({ slide: 6 })).toBeGreaterThan(pitch({ slide: 6, load: 1.6 }) + 50);
  expect(pitch({ slide: 6 }, { pitch: TYRE_KINDS.scooter.pitch })).toBeGreaterThan(pitch({ slide: 6 }, { pitch: TYRE_KINDS.truck.pitch }) + 200);
  expect(tonality({ slide: 6, squeal: 1 })).toBeGreaterThan(tonality({ slide: 6, squeal: 0 }) + 4);
  // Gravel: ticks and scrub, no tone; and it is still clearly audible.
  expect(rms(render({ slide: 5, squeal: 0, grit: 1, rough: 0.9, speed: 12 }))).toBeGreaterThan(0.03);
  // Rolling noise alone follows speed.
  expect(rms(render({ speed: 24 }))).toBeGreaterThan(rms(render({ speed: 8 })) * 2);
  expect(rms(render({ speed: 24 }, 1.5, 7, { roll: 0 }))).toBe(0);
});

test('voice pool: the local vehicle always, then the nearest three audible within 70 m', () => {
  const c = (id: string, distance: number, audible = true, local = false, active = false) => ({ id, local, distance, active, audible });
  const picked = selectTyreVoices([c('far', 75), c('a', 30), c('me', 0, false, true), c('b', 10), c('quiet', 5, false), c('c', 60), c('d', 65)]);
  expect([...picked].sort()).toEqual(['a', 'b', 'c', 'me']); expect(picked.size).toBe(TYRE_VOICES);
  expect(selectTyreVoices([c('x', 75, true, false, true)]).has('x')).toBe(true);
  expect(selectTyreVoices([c('x', 75)]).has('x')).toBe(false);
  expect(tyreAudible(SILENT_TYRES)).toBe(false); expect(tyreAudible({ ...SILENT_TYRES, slide: 2 })).toBe(true);
  expect(tyreAudible({ ...SILENT_TYRES, speed: 10 })).toBe(true); expect(tyreAudible({ ...SILENT_TYRES, speed: 2 })).toBe(false);
});

test('truck chassis: the frame carries the tyres and the synth squeals at the truck pitch instead of a 1050 Hz sine', () => {
  const L0 = TROPHY_CHASSIS.staticLength, tracker = new ChassisTracker();
  const slide = (t: ChassisTracker, material: number) => { let f = t.step({ dt: 1 / 60, speed: 15, rearSlew: 0, wheels: [] });
    for (let i = 0; i < 12; i++) f = t.step({ dt: 1 / 60, speed: 15, rearSlew: 0, wheels: [0, 1, 2, 3].map(() => wheel(6, material, L0)) }); return f; };
  const frame = slide(tracker, MAT.road);
  expect(frame.tyres.slide).toBeCloseTo(6, 5); expect(frame.tyres.squeal).toBe(1);
  expect(slide(new ChassisTracker(), MAT.gravel).tyres.squeal).toBe(0);
  const synth = new ChassisSynth(5, SR), out = new Float32Array(SR * 1.5), block = new Float32Array(128);
  synth.frame({ ...frame, hum: 0 });
  for (let i = 0; i < out.length; i += 128) { block.fill(0); synth.render(block); out.set(block.subarray(0, Math.min(128, out.length - i)), i); }
  const bins = spectrum(out.subarray(SR / 2), 300, 4000), top = bins.reduce((a, b) => (b.power > a.power ? b : a)).hz;
  expect(top).toBeGreaterThan(680); expect(top).toBeLessThan(1040); // 0.8 x the car's 0.85-1.3 kHz cluster
  expect(db(band(bins, 1450, 2000) / band(bins, 680, 1040))).toBeGreaterThan(-15); // harmonics, which the sine did not have
});

test('gameplay: a driven car on asphalt carries its slide in the car event; on foot it only rolls', () => {
  const events: AudioEvent[] = [], adapter = new GameplayAudio(e => events.push(e));
  const position = { x: 0, y: 1, z: 0 };
  const frame = (tick: number, mode: 'foot' | 'car', lateral: number) => ({ tick, time: tick / 60, paused: false, mode, connected: false, character: null,
    vehicle: { kind: 'car' as const, pos: position, rot: { x: 0, y: 0, z: 0, w: 1 }, linvel: { x: 12, y: 0, z: 0 }, speed: 12, forwardSpeed: 12,
      wheels: four(lateral), curbStrike: null, impact: null },
    input: { throttle: 0.5, brake: 0, steer: 0.4, handbrake: false }, position, forward: { x: 0, y: 0, z: -1 }, up: { x: 0, y: 1, z: 0 } });
  const tyres = () => { const e = events.filter(e => e.type === 'car').at(-1); return e?.type === 'car' ? e.tyres : SILENT_TYRES; };
  for (let tick = 1; tick <= 8; tick++) adapter.update(frame(tick, 'car', 0.4));
  expect(tyres().slide).toBe(0); expect(tyres().speed).toBe(12);
  // 50 ms of sliding on settled wheels is enough: no 0.18 s hold as for the tyre marks.
  for (let tick = 9; tick <= 12; tick++) adapter.update(frame(tick, 'car', 5));
  expect(tyres().slide).toBeCloseTo(5, 5);
  adapter.update(frame(13, 'foot', 5)); expect(tyres().slide).toBe(0); expect(tyres().speed).toBe(12);
});

class Param { value = 0; setTargetAtTime(v: number) { this.value = v; } setValueAtTime(v: number) { this.value = v; } linearRampToValueAtTime(v: number) { this.value = v; } cancelScheduledValues() {} }
class FakeNode {
  gain = new Param(); frequency = new Param(); Q = new Param(); threshold = new Param(); ratio = new Param(); playbackRate = new Param();
  positionX = new Param(); positionY = new Param(); positionZ = new Param();
  connected: unknown[] = []; disconnected = false; posted: unknown[] = []; options: unknown;
  port = { postMessage: (m: unknown) => { this.posted.push(m); } };
  type = ''; fftSize = 0; panningModel = ''; distanceModel = ''; refDistance = 0; maxDistance = 0; rolloffFactor = 0; buffer: unknown; loop = false; onended?: () => void;
  connect(n: unknown) { this.connected.push(n); return n; } disconnect() { this.disconnected = true; } start() {} stop() {}
}
test('GameAudio: the driven car gets a tyre voice, a truck does not, cars of other players are voiced when audible and near', async () => {
  const nodes: FakeNode[] = [], worklets: FakeNode[] = [], modules: string[] = [];
  const make = () => { const n = new FakeNode(); nodes.push(n); return n; };
  const ctx = { state: 'suspended', currentTime: 1, destination: new FakeNode(), listener: { setPosition() {}, setOrientation() {} },
    audioWorklet: { addModule: async (url: string) => { modules.push(url); } },
    createGain: make, createDynamicsCompressor: make, createBufferSource: make, createPanner: make, createBiquadFilter: make, createAnalyser: make, createOscillator: make,
    resume: async () => { ctx.state = 'running'; }, close: async () => { ctx.state = 'closed'; }, decodeAudioData: async () => ({}) };
  const g = globalThis as unknown as { AudioWorkletNode?: unknown };
  const before = g.AudioWorkletNode;
  g.AudioWorkletNode = class extends FakeNode { constructor(_c: unknown, public name: string, options: unknown) { super(); this.options = options; worklets.push(this); } };
  try {
    let now = 0;
    const audio = new GameAudio(() => ctx as unknown as AudioContext, (async () => ({ ok: false, status: 404 })) as unknown as typeof fetch, undefined, () => ++now);
    await audio.unlock();
    const at = { x: 0, y: 1, z: 0 };
    const listen = (paused = false) => audio.emit({ type: 'listener', position: at, forward: { x: 0, y: 0, z: -1 }, up: { x: 0, y: 1, z: 0 }, paused });
    const car = (kind: 'car' | 'scooter' | 'truck', slide: number) => audio.emit({ type: 'car', kind, position: at, rpm: 3000, throttle: 1, speed: 12, running: true,
      tyres: { ...SILENT_TYRES, slide, speed: 12 } });
    listen(); car('car', 5);
    await new Promise(resolve => setTimeout(resolve, 20)); // the worklet module import
    expect(modules.filter(url => url.includes('tyre.worklet'))).toHaveLength(1); // beside the truck engine's module
    listen(); car('car', 5);
    expect(audio.snapshot().tyres).toMatchObject({ ready: true, voices: [{ id: 'local', kind: 'car', local: true, slide: 5 }] });
    expect(worklets).toHaveLength(1);
    expect(worklets[0].options).toMatchObject({ processorOptions: { pitch: 1, roll: TYRE_KINDS.car.roll } });
    expect(worklets[0].posted.at(-1)).toMatchObject({ slide: 5, speed: 12 });
    // The same frame object again is not re-posted; standing still posts one silent frame and then nothing.
    const posted = worklets[0].posted.length, same = { ...SILENT_TYRES, slide: 3, speed: 9 };
    for (let i = 0; i < 3; i++) audio.emit({ type: 'car', kind: 'car', position: at, rpm: 900, throttle: 0, speed: 9, running: true, tyres: same });
    expect(worklets[0].posted.length).toBe(posted + 1);
    for (let i = 0; i < 5; i++) audio.emit({ type: 'car', kind: 'car', position: at, rpm: 900, throttle: 0, speed: 0, running: true, tyres: { ...SILENT_TYRES } });
    expect(worklets[0].posted.length).toBe(posted + 2);
    // Pausing silences the voice; resuming restores the class gain.
    const gain = worklets[0].connected[0] as FakeNode;
    car('car', 5); expect(gain.gain.value).toBeCloseTo(TYRE_KINDS.car.gain);
    listen(true); expect(gain.gain.value).toBe(0);
    listen(false); expect(gain.gain.value).toBeCloseTo(TYRE_KINDS.car.gain);
    // Another player's car: voiced within 70 m while it slides or rolls fast; a parked one never.
    const remote = (id: string, x: number, slide: number, speed: number) => audio.tyreVoice(id, { kind: 'car', local: false, position: { x, y: 1, z: 0 }, frame: { ...SILENT_TYRES, slide, speed } });
    remote('remote:a', 30, 6, 14); remote('remote:parked', 10, 0, 0); remote('remote:far', 200, 6, 14);
    expect(audio.snapshot().tyres.voices.map(v => v.id).sort()).toEqual(['local', 'remote:a']);
    expect(worklets).toHaveLength(2);
    expect(worklets[1].posted.at(-1)).toMatchObject({ slide: 6 });
    audio.tyreVoice('remote:a', null);
    expect(audio.snapshot().tyres.voices.map(v => v.id)).toEqual(['local']);
    // Switching to the truck releases the car's tyre voice: the truck's tyres are inside its engine voice.
    car('truck', 5);
    expect(audio.snapshot().tyres.voices).toEqual([]);
    car('scooter', 2);
    expect(audio.snapshot().tyres.voices).toMatchObject([{ id: 'local', kind: 'scooter' }]);
    expect(worklets.at(-1)!.options).toMatchObject({ processorOptions: { pitch: TYRE_KINDS.scooter.pitch } });
    expect(audio.errors.filter(e => !e.includes('HTTP 404'))).toEqual([]);
    audio.dispose();
    expect(worklets.at(-1)!.disconnected).toBe(true);
  } finally { g.AudioWorkletNode = before; }
});
