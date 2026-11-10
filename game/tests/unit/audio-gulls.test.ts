import { expect, test } from 'vitest';
import { GameAudio } from '../../src/audio';
import { CALL_GAP, FIRST_CALL, GULL_SOUNDS, GullScheduler, WATERS, airCutoff, echoShare, gullPresence, insidePolygon, nearestOnLine, pointAlong,
  waterReflection, type GullCall } from '../../src/audio/gulls';
import { SOUNDS } from '../../src/audio/mix';

/** Deterministic uniform [0, 1). */
function rng(seed: number) { let a = seed >>> 0; return () => { a = a + 0x6D2B79F5 >>> 0; let t = a; t = Math.imul(t ^ t >>> 15, t | 1); t ^= t + Math.imul(t ^ t >>> 7, t | 61); return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
const turku = WATERS.turku!;
// Engine metres (x east, z south; the office spawn is 168, 246).
const QUAY = { x: 250, y: 3, z: 300 }, TERMINAL = { x: -2400, y: 3, z: 1800 }, SUBURB = { x: 2500, y: 20, z: 2500 };
/** Four blocks into the city from the river (400 m from its centreline). */
const INLAND = (() => { const p = pointAlong(WATERS.turku!.river, 5200); return { x: p.x + p.nx * 400, y: 8, z: p.z + p.nz * 400 }; })();

test('gulls belong to the river bank and the harbour: nowhere else, and only in Turku', () => {
  expect(gullPresence(QUAY)).toEqual({ presence: 1, where: 'river' });
  expect(gullPresence(TERMINAL)).toEqual({ presence: 1, where: 'harbour' });
  // Four blocks from the Aura there are no gulls; nor in a suburb, nor on the river of another city.
  expect(nearestOnLine(turku.river, INLAND.x, INLAND.z).distance).toBeCloseTo(400, 0);
  expect(gullPresence(INLAND).presence).toBe(0); expect(gullPresence(SUBURB)).toEqual({ presence: 0, where: null });
  expect(gullPresence(QUAY, 'amsterdam').presence).toBe(0);
  // The presence fades between 60 and 130 m from the river's centreline, and between 150 and 350 m outside the basin
  // (the Tallink Silja terminal's forecourt, 116 m from the water, is in).
  expect(gullPresence({ x: -2364, y: 2, z: 1560 })).toEqual({ presence: 1, where: 'harbour' });
  expect(gullPresence({ x: -2520, y: 3, z: 746 - 250 }).presence).toBeCloseTo(0.5, 1); expect(gullPresence({ x: -2520, y: 3, z: 746 - 360 }).presence).toBe(0);
  const off = (metres: number) => { const p = pointAlong(turku.river, 5200); return gullPresence({ x: p.x + p.nx * metres, y: 3, z: p.z + p.nz * metres }).presence; };
  expect(off(40)).toBe(1); expect(off(95)).toBeCloseTo(0.5, 1); expect(off(140)).toBe(0);
  expect(insidePolygon(turku.harbour, -2700, 1700)).toBe(true); expect(insidePolygon(turku.harbour, -2000, 1500)).toBe(false);
  // The whole urban river is covered: from the rapids to the mouth, where it meets the harbour basin.
  expect(gullPresence({ x: 2100, y: 5, z: -1560 }).where).toBe('river'); expect(gullPresence({ x: -1900, y: 2, z: 1930 }).presence).toBe(1);
});

function listen(scheduler: GullScheduler, position: typeof QUAY, from: number, seconds: number, step = 1 / 30) {
  const calls: { at: number; call: GullCall }[] = [];
  for (let t = from; t < from + seconds; t += step) { const call = scheduler.tick(position, t); if (call) calls.push({ at: t, call }); }
  return calls;
}

test('gulls call now and then: a first call 10-30 s after arriving, then one every minute or two, never overlapping', () => {
  for (const seed of [1, 2, 3, 4, 5]) {
    const calls = listen(new GullScheduler(rng(seed)), QUAY, 100, 3600);
    expect(calls[0].at - 100).toBeGreaterThanOrEqual(FIRST_CALL[0]); expect(calls[0].at - 100).toBeLessThanOrEqual(FIRST_CALL[1] + 0.1);
    // An hour by the river: 25 to 60 calls, each starting 45-120 s after the previous one ended.
    expect(calls.length).toBeGreaterThanOrEqual(25); expect(calls.length).toBeLessThanOrEqual(60);
    for (let i = 1; i < calls.length; i++) {
      const gap = calls[i].at - calls[i - 1].at - (calls[i - 1].call.sound === 'gull-herring-longcall' ? 11.7 : 9.3);
      expect(gap).toBeGreaterThanOrEqual(CALL_GAP[0] - 0.1); expect(gap).toBeLessThanOrEqual(CALL_GAP[1] + 0.1);
    }
    // Both calls are used, and each is a slightly different bird.
    for (const sound of GULL_SOUNDS) expect(calls.filter(c => c.call.sound === sound).length).toBeGreaterThan(calls.length * 0.25);
    expect(new Set(calls.map(c => c.call.rate.toFixed(4))).size).toBeGreaterThan(calls.length * 0.8);
    for (const c of calls) { expect(c.call.rate).toBeGreaterThanOrEqual(0.95); expect(c.call.rate).toBeLessThanOrEqual(1.05); }
  }
  // The harbour is busier, the edge of the zone quieter; away from the water nothing is ever scheduled.
  const river = listen(new GullScheduler(rng(9)), QUAY, 0, 7200).length, harbour = listen(new GullScheduler(rng(9)), TERMINAL, 0, 7200).length;
  const p = pointAlong(turku.river, 5200), edge = listen(new GullScheduler(rng(9)), { x: p.x + p.nx * 120, y: 3, z: p.z + p.nz * 120 }, 0, 7200).length;
  expect(harbour).toBeGreaterThan(river * 1.15); expect(edge).toBeLessThan(river * 0.75);
  expect(listen(new GullScheduler(rng(9)), INLAND, 0, 7200)).toEqual([]);
});

test('each call is placed over the water near the listener; leaving the water cancels the wait', () => {
  const river = listen(new GullScheduler(rng(11)), QUAY, 0, 7200).map(c => c.call);
  for (const call of river) {
    expect(call.where).toBe('river');
    expect(nearestOnLine(turku.river, call.position.x, call.position.z).distance).toBeLessThanOrEqual(31); // over the river, banks are 40-50 m out
    expect(call.position.y).toBeGreaterThanOrEqual(12); expect(call.position.y).toBeLessThanOrEqual(35);
    expect(call.distance).toBeGreaterThan(40); expect(call.distance).toBeLessThan(260);
  }
  const harbour = listen(new GullScheduler(rng(12)), TERMINAL, 0, 7200).map(c => c.call);
  for (const call of harbour) {
    expect(call.where).toBe('harbour'); // over the basin, or on its edge when no point near the listener is inside
    expect(insidePolygon(turku.harbour, call.position.x, call.position.z)
      || nearestOnLine([...turku.harbour, turku.harbour[0]], call.position.x, call.position.z).distance < 1).toBe(true);
    expect(call.distance).toBeGreaterThan(55); expect(call.distance).toBeLessThan(270);
  }
  // From a quay just outside the basin the bird is over its nearest edge.
  const outside = listen(new GullScheduler(() => 0.999), { x: -1884, y: 3, z: 2044 }, 0, 600).map(c => c.call);
  expect(outside.length).toBeGreaterThan(0);
  for (const call of outside) expect(nearestOnLine([...turku.harbour, turku.harbour[0]], call.position.x, call.position.z).distance).toBeLessThan(1);
  // Walking away before the first call, and back: the wait starts over.
  const scheduler = new GullScheduler(rng(13));
  expect(listen(scheduler, QUAY, 0, 9)).toEqual([]); expect(scheduler.wait(9)).toBeLessThan(30);
  scheduler.tick(INLAND, 9.1); expect(scheduler.wait(9.1)).toBe(Infinity);
  expect(listen(scheduler, QUAY, 10, 9.9)).toEqual([]);
});

test('distance treatment: air absorption, echo share and the reflection off the water', () => {
  expect(airCutoff(50)).toBeGreaterThan(9000); expect(airCutoff(150)).toBeCloseTo(5366, -1); expect(airCutoff(300)).toBeLessThan(3200);
  expect(echoShare(30)).toBeLessThan(0.25); expect(echoShare(200)).toBeGreaterThan(0.8); expect(echoShare(1000)).toBe(0.85);
  // A bird 20 m up: the reflection is 2.3 ms late at 100 m and closes up with distance.
  expect(waterReflection(100, 20)).toBeGreaterThan(0.002); expect(waterReflection(100, 20)).toBeLessThan(0.003);
  expect(waterReflection(400, 20)).toBeLessThan(waterReflection(100, 20) / 3);
});

class Param { value = 0; setTargetAtTime(v: number) { this.value = v; } setValueAtTime(v: number) { this.value = v; } linearRampToValueAtTime(v: number) { this.value = v; } cancelScheduledValues() {} }
class FakeNode {
  gain = new Param(); frequency = new Param(); Q = new Param(); threshold = new Param(); ratio = new Param(); playbackRate = new Param(); delayTime = new Param();
  positionX = new Param(); positionY = new Param(); positionZ = new Param();
  kind = ''; connected: FakeNode[] = []; disconnected = false; started = false; buffer: unknown; loop = false; onended?: () => void;
  type = ''; fftSize = 0; panningModel = ''; distanceModel = ''; refDistance = 0; maxDistance = 0; rolloffFactor = 0;
  connect(n: FakeNode) { this.connected.push(n); return n; } disconnect() { this.disconnected = true; } start() { this.started = true; } stop() {}
}
test('GameAudio: the calls are fetched near the water only, play as spatial one-shots, pause with the game and need no loop', async () => {
  const nodes: FakeNode[] = [], fetched: string[] = [];
  const make = (kind: string) => () => { const n = new FakeNode(); n.kind = kind; nodes.push(n); return n; };
  const ctx = { state: 'suspended', currentTime: 0, sampleRate: 8000, destination: new FakeNode(), listener: { setPosition() {}, setOrientation() {} },
    createGain: make('gain'), createDynamicsCompressor: make('compressor'), createBufferSource: make('source'), createPanner: make('panner'),
    createBiquadFilter: make('filter'), createAnalyser: make('analyser'), createOscillator: make('oscillator'), createDelay: make('delay'), createConvolver: make('convolver'),
    createBuffer: (channels: number, length: number) => ({ getChannelData: () => new Float32Array(length), numberOfChannels: channels }),
    resume: async () => { ctx.state = 'running'; }, close: async () => { ctx.state = 'closed'; }, decodeAudioData: async () => ({}) };
  const fetcher = (async (url: string) => { fetched.push(url); return { ok: true, arrayBuffer: async () => new ArrayBuffer(4) }; }) as unknown as typeof fetch;
  let now = 0;
  const audio = new GameAudio(() => ctx as unknown as AudioContext, fetcher, 'audio/', () => now);
  await audio.unlock();
  expect(SOUNDS).not.toContain('gulls'); expect(fetched).toHaveLength(SOUNDS.length);
  expect(fetched.some(url => url.includes('gull'))).toBe(false); // not part of what loads before the menu
  const at = (position: typeof QUAY, paused = false) => audio.emit({ type: 'listener', position, forward: { x: 0, y: 0, z: -1 }, up: { x: 0, y: 1, z: 0 }, paused });
  const flush = () => new Promise(resolve => setTimeout(resolve, 0));
  // Far from any water: nothing is fetched and nothing ever calls.
  for (now = 1; now < 600; now += 1) at(SUBURB);
  await flush();
  expect(fetched.some(url => url.includes('gull'))).toBe(false); expect(audio.snapshot().gulls).toMatchObject({ presence: 0, loaded: 0, calls: 0, nextIn: null });
  // Approaching the river (300 m out): the two calls are fetched ahead; still no call outside the zone.
  const p = pointAlong(turku.river, 5200), approach = { x: p.x + p.nx * 300, y: 3, z: p.z + p.nz * 300 };
  for (; now < 700; now += 1) at(approach);
  await flush();
  expect(fetched.filter(url => url.includes('gull')).sort()).toEqual(['audio/gull-common-longcall.ogg', 'audio/gull-herring-longcall.ogg']);
  expect(audio.snapshot().gulls).toMatchObject({ presence: 0, loaded: 2, calls: 0 });
  // On the quay: a first call within 10-30 s, as a one-shot through an air filter, a panner at the bird and the echo.
  const before = nodes.length;
  for (; now < 735 && !audio.snapshot().gulls.calls; now += 1 / 30) at(QUAY);
  const g = audio.snapshot().gulls;
  expect(g.calls).toBe(1); expect(g.playing).toMatch(/^gull-(herring|common)-longcall$/); expect(g.last).toMatchObject({ where: 'river' });
  const made = nodes.slice(before), source = made.find(n => n.kind === 'source')!, panner = made.find(n => n.kind === 'panner')!, filter = made.find(n => n.kind === 'filter')!;
  expect(source.started).toBe(true); expect(source.loop).toBe(false);
  expect([panner.positionX.value, panner.positionY.value, panner.positionZ.value].map(Math.round)).toEqual(g.last!.position);
  expect(panner.distanceModel).toBe('inverse'); expect(Math.abs(filter.frequency.value - airCutoff(g.last!.distance))).toBeLessThan(80); // the snapshot rounds the distance
  expect(made.some(n => n.kind === 'convolver')).toBe(true); expect(made.some(n => n.kind === 'delay')).toBe(true);
  expect(audio.snapshot().loopSounds).not.toContain('gulls');
  // While it plays no second call starts; pausing mutes it, resuming restores it.
  const level = source.connected[0].connected[0];
  expect(level.gain.value).toBe(1);
  at(QUAY, true); expect(level.gain.value).toBe(0); at(QUAY, false); now += 0.1; at(QUAY); expect(level.gain.value).toBe(1);
  for (const end = now + 8; now < end; now += 1 / 30) at(QUAY);
  expect(audio.snapshot().gulls.calls).toBe(1);
  // The call ends: its nodes are freed and the next one comes 45-120 s later.
  source.onended!();
  expect(audio.snapshot().gulls.playing).toBeNull(); expect(made.filter(n => n.kind !== 'convolver').every(n => n.disconnected)).toBe(true);
  const next = audio.snapshot().gulls.nextIn!;
  expect(next).toBeGreaterThan(25); expect(next).toBeLessThan(125);
  // While the world loads (a save being restored) nothing is scheduled: the wait does not run down into a call.
  audio.setLoading(true);
  for (const end = now + 300; now < end; now += 1) at(QUAY);
  expect(audio.snapshot().gulls.calls).toBe(1);
  audio.setLoading(false);
  for (const end = now + 4.9; now < end; now += 1 / 30) at(QUAY);
  expect(audio.snapshot().gulls.calls).toBe(1); // five more seconds after the loading ends
  for (const end = now + 1; now < end && audio.snapshot().gulls.calls < 2; now += 1 / 30) at(QUAY);
  expect(audio.snapshot().gulls.calls).toBe(2);
  // Likewise in the start menu.
  audio.setLoading(false, true);
  for (const end = now + 300; now < end; now += 1) at(QUAY);
  expect(audio.snapshot().gulls.calls).toBe(2);
  expect(audio.errors).toEqual([]);
  audio.dispose();
});
