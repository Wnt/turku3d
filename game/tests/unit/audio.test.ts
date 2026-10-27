import { expect, test } from 'vitest';
import { GameAudio } from '../../src/audio';
import { GameplayAudio, engineRpm } from '../../src/audio/gameplay';
import { ENGINE_SOUND, ambience, carMix, footstep, oneShot, SOUNDS, type AudioEvent } from '../../src/audio/mix';
import { MAT } from '../../src/shared/col/materials';

class Param {
  value = 0;
  setTargetAtTime(value: number) { this.value = value; }
}
class Node {
  gain = new Param(); playbackRate = new Param(); threshold = new Param(); ratio = new Param();
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
  node() { const node = new Node(); this.nodes.push(node); return node; }
  async resume() { this.state = 'running'; }
  async close() { this.state = 'closed'; }
  async decodeAudioData() { return {}; }
}
const position = { x: 168, y: 3, z: 246 };
const listener: AudioEvent = { type: 'listener', position, forward: { x: 0, y: 0, z: -1 }, up: { x: 0, y: 1, z: 0 }, paused: false };
const fetcher = (async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(4) })) as unknown as typeof fetch;

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

test('river fades with distance, engine follows RPM/throttle, rolling and slip are bounded', () => {
  const near = ambience({ x: 200, y: 4, z: 310 }), far = ambience({ x: -500, y: 4, z: -500 });
  expect(near.water).toBeGreaterThan(far.water); expect(far.water).toBe(0);
  expect(near.gulls).toBeGreaterThan(far.gulls); expect(near.city).toBeLessThan(far.city);
  expect(near.river).toEqual({ x: 200, y: 0, z: 310 });
  const idle = carMix(850, 0, 0, 0, true), fast = carMix(5000, 1, 20, 1, true);
  expect(fast.rate).toBeGreaterThan(idle.rate); expect(fast.engine).toBeGreaterThan(idle.engine);
  expect(idle.roll).toBe(0); expect(idle.screech).toBe(0); expect(fast.screech).toBeGreaterThan(0);
  expect(carMix(900, 0, 0, 1, false).engine).toBe(0);
  expect(Object.values(carMix(NaN, Infinity, NaN, Infinity, true)).every(Number.isFinite)).toBe(true);
  // The scooter buzzes higher than the car at the same estimated rpm and rolls more quietly.
  const scooter = carMix(3000, 0.5, 10, 0, true, 'scooter'), car = carMix(3000, 0.5, 10, 0, true, 'car');
  expect(scooter.rate).toBeGreaterThan(carMix(1500, 0.5, 10, 0, true, 'car').rate);
  expect(scooter.roll).toBeLessThan(car.roll);
  expect(ENGINE_SOUND).toEqual({ car: 'engine', scooter: 'scooter', truck: 'truck' });
  // The truck's V6 is the loudest engine layer and pitches lower than the car at the same rpm; its tyres roar.
  const truck = carMix(3000, 0.5, 10, 0, true, 'truck');
  expect(truck.engine).toBeGreaterThan(car.engine);
  expect(carMix(800, 0, 0, 0, true, 'truck').engine).toBeGreaterThan(carMix(800, 0, 0, 0, true, 'car').engine);
  expect(truck.rate).toBeLessThan(car.rate);
  expect(truck.roll).toBeGreaterThan(car.roll);
});

test('engine rpm: the truck idles low and pulls four gears to 120 km/h within the car rev range', () => {
  expect(engineRpm('truck', 0, 0)).toBeLessThan(engineRpm('car', 0, 0));
  const revs = Array.from({ length: 34 }, (_, v) => engineRpm('truck', v, 1));
  expect(Math.max(...revs)).toBeLessThan(6000);
  // Upshifts drop the revs at 9, 17 and 25 m/s.
  for (const shift of [9, 17, 25]) expect(revs[shift]).toBeLessThan(revs[shift - 1]);
  expect(engineRpm('scooter', 5, 0.5)).toBeGreaterThan(engineRpm('car', 5, 0.5));
});

test('fake AudioContext: lazy unlock, routing, smoothing, pause, mute, bounded voices and teardown', async () => {
  const ctx = new FakeContext(); let created = 0;
  const audio = new GameAudio(() => { created++; return ctx as unknown as AudioContext; }, fetcher);
  audio.emit(listener); audio.emit({ type: 'gamepad-connected' });
  expect(created).toBe(0); expect(audio.snapshot().state).toBe('locked');
  await audio.unlock(); await audio.unlock();
  expect(created).toBe(1); expect(audio.snapshot().loaded).toBe(SOUNDS.length);
  expect(audio.snapshot().loops).toBe(5); expect(audio.snapshot().played.connect).toBe(1);
  expect(ctx.nodes[0].connections[0].connections).toContain(ctx.destination);
  audio.emit({ type: 'car', kind: 'car', position, rpm: 3000, throttle: 1, speed: 12, slip: 0.7, running: true });
  // Every class's engine loop exists (car, scooter, truck) beside roll and screech; only the driven class's one is audible.
  expect(audio.snapshot().loops).toBe(10);
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
