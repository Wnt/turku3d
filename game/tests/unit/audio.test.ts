import { expect, test } from 'vitest';
import { GameAudio } from '../../src/audio';
import { GameplayAudio, engineRpm } from '../../src/audio/gameplay';
import { ENGINE_SOUND, SYNTH_ENGINE, ambience, carMix, crowdGain, footstep, oneShot, SOUNDS, type AudioEvent } from '../../src/audio/mix';
import { SILENT_TYRES } from '../../src/audio/engine/tyre';
import { MAT } from '../../src/shared/col/materials';

/** Keeps its scheduled event times like Chrome: an event leaves the list only by a cancel (the fake never renders). */
class Param {
  value = 0; targets = 0; events: number[] = [];
  setTargetAtTime(value: number, time = 0) { this.value = value; this.targets++; this.events.push(time); }
  setValueAtTime(value: number, time = 0) { this.value = value; this.events.push(time); }
  linearRampToValueAtTime(value: number, time = 0) { this.value = value; this.events.push(time); }
  cancelScheduledValues(time: number) { this.events = this.events.filter(t => t < time); }
}
class Node {
  gain = new Param(); playbackRate = new Param(); threshold = new Param(); ratio = new Param(); frequency = new Param();
  type = 'sine'; fftSize = 0;
  positionX = new Param(); positionY = new Param(); positionZ = new Param();
  buffer: unknown; loop = false; started = false; disconnected = false;
  onended?: () => void;
  connections: Node[] = [];
  connect(node: Node) { this.connections.push(node); }
  disconnect() { this.disconnected = true; }
  start() { this.started = true; }
  stop() { this.onended?.(); }
}
class FakeContext {
  state = 'suspended'; currentTime = 0; destination = new Node(); nodes: Node[] = [];
  listener = { positionX: new Param(), positionY: new Param(), positionZ: new Param(),
    forwardX: new Param(), forwardY: new Param(), forwardZ: new Param(), upX: new Param(), upY: new Param(), upZ: new Param() };
  createGain() { return this.node(); }
  createDynamicsCompressor() { return this.node(); }
  createBufferSource() { return this.node(); }
  createPanner() { return this.node(); }
  createBiquadFilter() { return this.node(); }
  createAnalyser() { return this.node(); }
  createOscillator() { return this.node(); }
  node() { const node = new Node(); this.nodes.push(node); return node; }
  async resume() { this.state = 'running'; }
  async close() { this.state = 'closed'; }
  async decodeAudioData() { return {}; }
}
const position = { x: 168, y: 3, z: 246 };
const listener: AudioEvent = { type: 'listener', position, forward: { x: 0, y: 0, z: -1 }, up: { x: 0, y: 1, z: 0 }, paused: false };
const fetcher = (async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(4) })) as unknown as typeof fetch;
/** A mix clock a second further on at every reading: every continuous event applies at once. */
const ticking = () => { let t = 0; return () => ++t; };

test('material contract maps five surfaces; sensors and silent boundaries never step', () => {
  for (const [id, sound] of [[MAT.road, 'asphalt'], [MAT.cobblestone, 'stone'], [MAT.curb, 'stone'],
    [MAT.stair_tread_helper, 'stone'], [MAT.grass, 'grass'], [MAT.gravel, 'gravel'], [MAT.wood_deck, 'wood']] as const) {
    expect(footstep(id)).toBe(`step-${sound}`);
  }
  expect(footstep(MAT.water)).toBeNull(); expect(footstep(MAT.invisible_boundary)).toBeNull();
  expect(footstep(255)).toBe('step-asphalt');
  expect(oneShot({ type: 'door', position, open: true })?.sound).toBe('door-open');
  expect(oneShot({ type: 'door', position, open: false })?.sound).toBe('door-close');
  const impact = (impulse: number) => oneShot({ type: 'impact', position, impulse })!.gain;
  expect(impact(1200)).toBeCloseTo(impact(600) * 2);
  expect(impact(1e9)).toBeLessThanOrEqual(1); expect(impact(NaN)).toBe(0);
});

test('river fades with distance, engine follows RPM/throttle and stays bounded', () => {
  const near = ambience({ x: 200, y: 4, z: 310 }), far = ambience({ x: -500, y: 4, z: -500 });
  expect(near.water).toBeGreaterThan(far.water); expect(far.water).toBe(0);
  expect(near.gulls).toBeGreaterThan(far.gulls); expect(near.city).toBeLessThan(far.city);
  expect(near.river).toEqual({ x: 200, y: 0, z: 310 });
  const idle = carMix(850, 0, true), fast = carMix(5000, 1, true);
  expect(fast.rate).toBeGreaterThan(idle.rate); expect(fast.engine).toBeGreaterThan(idle.engine);
  expect(carMix(900, 0, false).engine).toBe(0);
  expect(Object.values(carMix(NaN, Infinity, true)).every(Number.isFinite)).toBe(true);
  // The scooter buzzes higher than the car at the same estimated rpm.
  expect(carMix(3000, 0.5, true, 'scooter').rate).toBeGreaterThan(carMix(1500, 0.5, true, 'car').rate);
  // The trophy truck's engine, tyres and slides are synthesised by its engine voice; its sample loops stay silent.
  expect(ENGINE_SOUND).toEqual({ car: 'engine', scooter: 'scooter', truck: SYNTH_ENGINE });
  expect(SOUNDS).not.toContain('truck');
  expect(carMix(3000, 0.5, true, 'truck')).toEqual({ rate: 1, engine: 0 });
  // Tyres are synthesised (engine/tyre.ts): no rolling or squeal sample is loaded.
  expect(SOUNDS).not.toContain('roll'); expect(SOUNDS).not.toContain('screech');
});

test('engine rpm: the car and scooter keep their gear approximations', () => {
  expect(engineRpm('scooter', 5, 0.5)).toBeGreaterThan(engineRpm('car', 5, 0.5));
  expect(engineRpm('car', 9, 1)).toBeLessThan(engineRpm('car', 7.9, 1));
});

test('fake AudioContext: lazy unlock, routing, smoothing, pause, mute, bounded voices and teardown', async () => {
  const ctx = new FakeContext(); let created = 0;
  const audio = new GameAudio(() => { created++; return ctx as unknown as AudioContext; }, fetcher, undefined, ticking());
  audio.emit(listener); audio.emit({ type: 'gamepad-connected' });
  expect(created).toBe(0); expect(audio.snapshot().state).toBe('locked');
  await audio.unlock(); await audio.unlock();
  expect(created).toBe(1); expect(audio.snapshot().loaded).toBe(SOUNDS.length);
  expect(audio.snapshot().loops).toBe(5); expect(audio.snapshot().played.connect).toBe(1);
  expect(ctx.nodes[0].connections[0].connections).toContain(ctx.destination);
  audio.emit({ type: 'car', kind: 'car', position, rpm: 3000, throttle: 1, speed: 12, tyres: { ...SILENT_TYRES, slide: 4, speed: 12 }, running: true });
  // Both sample engine loops exist (car, scooter); only the driven class's one is audible. The tyres are a synth voice.
  expect(audio.snapshot().loops).toBe(7);
  // The walkers' crowd murmur joins the beds once the walker system reports a count.
  audio.emit({ type: 'crowd', walkers: 6 });
  expect(audio.snapshot().loops).toBe(8);
  audio.emit({ ...listener, position: { x: 1, y: 2, z: 3 } });
  expect(ctx.listener.positionX.value).toBe(1); expect(ctx.listener.forwardZ.value).toBe(-1);
  audio.emit({ type: 'volume', channel: 'master', value: 0.4 });
  audio.emit({ type: 'mute' }); expect(ctx.nodes[0].gain.value).toBe(0);
  audio.emit({ type: 'volume', channel: 'vehicle', value: 5 }); expect(audio.volumes.vehicle).toBe(1);
  audio.emit({ type: 'mute' }); expect(ctx.nodes[0].gain.value).toBe(0.4);
  audio.emit({ ...listener, paused: true });
  const count = audio.snapshot().voices;
  audio.emit({ type: 'footstep', position, speed: 3, material: MAT.road });
  expect(audio.snapshot().voices).toBe(count);
  audio.emit(listener);
  for (let i = 0; i < 50; i++) audio.emit({ type: 'impact', position, impulse: 3000 });
  expect(audio.snapshot().voices).toBeLessThanOrEqual(24);
  expect(audio.errors).toEqual([]);
  audio.dispose(); expect(ctx.state).toBe('closed'); expect(audio.snapshot().voices).toBe(0);
  expect(ctx.nodes.filter(n => n.started).every(n => n.disconnected)).toBe(true);
});

test('Firefox legacy listener and panner positioning work without spatial AudioParams', async () => {
  const ctx = new FakeContext();
  const listenerPositions: number[][] = [], orientations: number[][] = [], pannerPositions: number[][] = [];
  // Firefox AudioListener lacks positionX/forwardX/upX but exposes the legacy methods.
  ctx.listener = {
    setPosition: (x: number, y: number, z: number) => { listenerPositions.push([x, y, z]); },
    setOrientation: (fx: number, fy: number, fz: number, ux: number, uy: number, uz: number) => {
      orientations.push([fx, fy, fz, ux, uy, uz]);
    },
  } as unknown as FakeContext['listener'];
  ctx.createPanner = () => {
    const node = ctx.node();
    Object.assign(node, {
      positionX: undefined, positionY: undefined, positionZ: undefined,
      setPosition: (x: number, y: number, z: number) => { pannerPositions.push([x, y, z]); },
    });
    return node;
  };

  const audio = new GameAudio(() => ctx as unknown as AudioContext, fetcher, undefined, ticking());
  audio.emit(listener); // Can arrive before the context exists.
  await audio.unlock();
  audio.emit(listener); // The render loop must update a legacy listener without throwing.
  expect(listenerPositions).toEqual([[position.x, position.y, position.z]]);
  expect(orientations).toEqual([[0, 0, -1, 0, 1, 0]]);
  // Spatial street traffic bed: at the cars within 150 m, at the listener (silent) before any traffic event.
  expect(pannerPositions).toContainEqual([position.x, position.y, position.z]);
  const moved = { x: 9, y: 4, z: 5 };
  audio.emit({ ...listener, position: moved, forward: { x: 1, y: 0, z: 0 } });
  audio.emit({ type: 'car', kind: 'car', position: moved, rpm: 3000, throttle: 0.5,
    speed: 10, tyres: SILENT_TYRES, running: true });
  expect(listenerPositions.at(-1)).toEqual([9, 4, 5]);
  expect(orientations.at(-1)).toEqual([1, 0, 0, 0, 1, 0]);
  expect(pannerPositions).toContainEqual([9, 4, 5]);
  expect(audio.errors).toEqual([]);
  audio.dispose();
});

test('ambient traffic: at most eight engine voices follow the nearest cars, each car keeps its voice', async () => {
  const ctx = new FakeContext();
  const audio = new GameAudio(() => ctx as unknown as AudioContext, fetcher, undefined, ticking());
  await audio.unlock();
  audio.emit(listener);
  const car = (id: number, d: number, speed = 8) => ({ id, position: { x: position.x + d, y: position.y, z: position.z }, speed });
  const send = (cars: ReturnType<typeof car>[]) => audio.emit({ type: 'traffic', cars, count: cars.length, centre: cars[0]?.position ?? null });
  send(Array.from({ length: 10 }, (_, i) => car(i + 1, 10 + i * 5)));
  expect(audio.snapshot().trafficVoices).toBe(8);
  // Engines are positional loops on the vehicle bus, pitched by speed.
  const engines = ctx.nodes.filter(n => n.loop && n.started);
  expect(engines.length).toBeGreaterThanOrEqual(8);
  const pitched = (speed: number) => { send([car(1, 10, speed)]); return Math.max(...engines.map(n => n.playbackRate.value)); };
  expect(pitched(14)).toBeGreaterThan(pitched(2));
  expect(audio.snapshot().trafficVoices).toBe(1);
  send([]);
  expect(audio.snapshot().trafficVoices).toBe(0);
  audio.dispose();
});

test('asset failure is contained, and disposal during decode never starts voices', async () => {
  const ctx = new FakeContext();
  const audio = new GameAudio(() => ctx as unknown as AudioContext,
    (async () => ({ ok: false, status: 404 })) as unknown as typeof fetch);
  await audio.unlock(); expect(audio.errors).toHaveLength(SOUNDS.length); expect(audio.snapshot().loops).toBe(0);
  audio.dispose();
  const next = new GameAudio(() => new FakeContext() as unknown as AudioContext, fetcher);
  next.emit(listener); const load = next.unlock(); next.dispose(); await load;
  expect(next.snapshot().voices).toBe(0); expect(next.snapshot().loaded).toBe(0);
});

test('gameplay emits by travelled distance, suppresses duplicate ticks/airborne/pause and deduplicates impacts', () => {
  const events: AudioEvent[] = [], adapter = new GameplayAudio(e => events.push(e));
  const frame = { tick: 1, time: 1, paused: false, mode: 'foot' as 'foot' | 'car', connected: true,
    character: { pos: { ...position }, vel: { x: 3, y: 0, z: 0 }, grounded: true, enabled: true, groundMaterial: MAT.gravel },
    vehicle: { pos: position, rot: { x: 0, y: 0, z: 0, w: 1 }, linvel: { x: 3, y: 0, z: 0 }, speed: 3, forwardSpeed: 3,
      wheels: [], curbStrike: { id: 1, wheel: 0, height: 0.1, speed: 3, loss: 0.1, blocked: false, rumble: 0.2 },
      impact: { id: 1, impulse: 1200 } },
    input: null, position, forward: { x: 0, y: 0, z: -1 }, up: { x: 0, y: 1, z: 0 } };
  adapter.update(frame); adapter.update(frame);
  frame.tick++; frame.character.pos.x += 1; adapter.update(frame); adapter.update(frame);
  expect(events.filter(e => e.type === 'footstep')).toHaveLength(1);
  expect(events.filter(e => e.type === 'curb')).toHaveLength(1);
  expect(events.filter(e => e.type === 'impact')).toHaveLength(1);
  expect(events.filter(e => e.type === 'gamepad-connected')).toHaveLength(1);
  frame.tick++; frame.character.grounded = false; frame.character.pos.x++; adapter.update(frame);
  frame.tick++; frame.character.grounded = true; frame.paused = true; frame.character.pos.x++; adapter.update(frame);
  expect(events.filter(e => e.type === 'footstep')).toHaveLength(1);
  frame.paused = false; frame.mode = 'car'; adapter.update(frame);
  frame.time += 0.4; adapter.update(frame);
  expect(events.filter(e => e.type === 'door').map(e => e.type === 'door' && e.open)).toEqual([true, false]);
});

test('gameplay: the scooter has no doors and revs its own engine loop higher than the car', () => {
  const events: AudioEvent[] = [], adapter = new GameplayAudio(e => events.push(e));
  const vehicle = (kind: 'car' | 'scooter') => ({ kind, pos: position, rot: { x: 0, y: 0, z: 0, w: 1 }, linvel: { x: 6, y: 0, z: 0 },
    speed: 6, forwardSpeed: 6, wheels: [], curbStrike: null, impact: null });
  const frame = (kind: 'car' | 'scooter', tick: number, mode: 'foot' | 'car') => ({ tick, time: tick, paused: false, mode, connected: false,
    character: null, vehicle: vehicle(kind), input: { throttle: 0.5, brake: 0, steer: 0, handbrake: false },
    position, forward: { x: 0, y: 0, z: -1 }, up: { x: 0, y: 1, z: 0 } });
  adapter.update(frame('scooter', 1, 'car')); adapter.update(frame('scooter', 2, 'foot'));
  expect(events.some(e => e.type === 'door')).toBe(false);
  const engines = events.filter(e => e.type === 'car');
  expect(engines.every(e => e.type === 'car' && e.kind === 'scooter')).toBe(true);
  adapter.update(frame('car', 3, 'car'));
  const [scooterRpm, carRpm] = [engines[0], events.filter(e => e.type === 'car').at(-1)].map(e => e?.type === 'car' ? e.rpm : 0);
  expect(scooterRpm).toBeGreaterThan(carRpm);
});

test('preload decodes while suspended; the start gesture only resumes and reuses buffers', async () => {
  const ctx = new FakeContext();
  let fetched = 0;
  const audio = new GameAudio(() => ctx as unknown as AudioContext, (...args) => { fetched++; return fetcher(...args); });
  await audio.preload();
  expect(ctx.state).toBe('suspended');
  expect(audio.snapshot().loaded).toBe(SOUNDS.length);
  const before = fetched;
  await audio.unlock();
  expect(ctx.state).toBe('running');
  expect(fetched).toBe(before);
  audio.dispose();
});

test('crowd murmur follows the drawn walkers near the camera', () => {
  expect(crowdGain(0)).toBe(0);
  expect(crowdGain(6)).toBeCloseTo(0.065, 6);
  expect(crowdGain(12)).toBeCloseTo(0.13, 6); expect(crowdGain(40)).toBeCloseTo(0.13, 6);
});

test('continuous mix: at most 30 updates a second, unchanged targets skipped, bounded events, nothing while suspended', async () => {
  const ctx = new FakeContext();
  let now = 0;
  const audio = new GameAudio(() => ctx as unknown as AudioContext, fetcher, undefined, () => now);
  await audio.preload();
  const params = () => ctx.nodes.flatMap(n => [n.gain, n.playbackRate, n.frequency, n.positionX, n.positionY, n.positionZ])
    .concat(Object.values(ctx.listener));
  const frame = (i: number, moving: boolean) => {
    const at = { x: position.x + (moving ? i * 0.5 : 0), y: position.y, z: position.z };
    audio.emit({ ...listener, position: at });
    audio.emit({ type: 'traffic', cars: [{ id: 1, position: { ...at, x: at.x + 20 }, speed: 8 }], count: 1, centre: at });
    audio.emit({ type: 'car', kind: 'car', position: at, rpm: 2000, throttle: 0.5, speed: moving ? 8 : 0, tyres: SILENT_TYRES, running: true });
  };
  // Suspended (no user gesture yet): 10 s of frames schedule nothing, so no event list grows.
  const before = params().reduce((n, p) => n + p.targets, 0);
  for (let i = 0; i < 600; i++) { now = i / 60; frame(i, true); }
  expect(params().reduce((n, p) => n + p.targets, 0)).toBe(before);
  await audio.unlock();
  // Running, moving at 30 m/s: the listener follows at 30 Hz, not at the 60 Hz frame rate.
  const x = ctx.listener.positionX;
  const start = x.targets;
  for (let i = 0; i < 120; i++) { now = 10 + i / 60; frame(i, true); }
  expect(x.targets - start).toBeGreaterThanOrEqual(59);
  expect(x.targets - start).toBeLessThanOrEqual(61);
  // The latest target lags the frames by at most one mix tick (two frames).
  expect(x.value).toBeGreaterThanOrEqual(position.x + 117 * 0.5);
  // Standing still: after the first tick no target changes, so nothing is rescheduled.
  for (let i = 0; i < 4; i++) { now = 20 + i / 30; frame(0, false); }
  const settled = params().reduce((n, p) => n + p.targets, 0);
  for (let i = 4; i < 120; i++) { now = 20 + i / 30; frame(0, false); }
  expect(params().reduce((n, p) => n + p.targets, 0)).toBe(settled);
  // A context clock that stands still (the suspended case) never piles events up on a param.
  for (let i = 0; i < 300; i++) { now = 30 + i / 30; frame(i, true); }
  // (Music notes keep their own one-shot envelopes; every param the mix drives holds at most its latest target.)
  const driven = params().filter(p => p.targets > 5);
  expect(driven.length).toBeGreaterThan(10);
  expect(Math.max(...driven.map(p => p.events.length))).toBeLessThanOrEqual(1);
  expect(audio.errors).toEqual([]);
  audio.dispose();
});
