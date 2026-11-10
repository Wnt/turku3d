import { expect, test } from 'vitest';
import { LOBBY_SETTLE, MusicSystem, PLAYLISTS, SETTLE, TRACKS, hurry, type MusicElement } from '../../src/audio/music';

class Param {
  value = 0; events: { type: string; value: number; time: number; constant?: number }[] = [];
  setTargetAtTime(value: number, time: number, constant: number) { this.value = value; this.events.push({ type: 'target', value, time, constant }); }
  setValueAtTime(value: number, time: number) { this.value = value; this.events.push({ type: 'set', value, time }); }
  linearRampToValueAtTime(value: number, time: number) { this.value = value; this.events.push({ type: 'ramp', value, time }); }
  cancelScheduledValues() {}
}
class Node { gain = new Param(); frequency = new Param(); type = 'sine'; onended?: () => void; starts: number[] = [];
  connect(node: Node) { return node; } disconnect() {} start(t: number) { this.starts.push(t); } stop() {} }
class FakeElement implements MusicElement {
  src = ''; preload = ''; loop = false; currentTime = 0; playbackRate = 1; preservesPitch = false; paused = true; ended = false; plays = 0;
  listeners: Record<string, (() => void)[]> = {};
  async play() { this.paused = false; this.ended = false; this.plays++; }
  pause() { this.paused = true; }
  addEventListener(type: string, listener: () => void) { (this.listeners[type] ??= []).push(listener); }
  fire(type: 'ended' | 'error') { if (type === 'ended') { this.ended = true; this.paused = true; } for (const l of this.listeners[type] ?? []) l(); }
}
class FakeContext { state = 'running'; currentTime = 0; destination = new Node(); nodes: Node[] = []; oscillators: Node[] = [];
  createGain() { return this.node(); } createBiquadFilter() { return this.node(); } createMediaElementSource() { return this.node(); }
  createOscillator() { const n = this.node(); this.oscillators.push(n); return n; }
  node() { const n = new Node(); this.nodes.push(n); return n; } async resume() {} }

function setup() {
  const ctx = new FakeContext(), elements: FakeElement[] = [];
  const music = new MusicSystem(ctx as unknown as AudioContext, undefined, { base: 'm/', element: () => { const e = new FakeElement(); elements.push(e); return e; } });
  const advance = (seconds: number) => { const end = ctx.currentTime + seconds; while (ctx.currentTime < end - 1e-9) { ctx.currentTime = Math.min(end, ctx.currentTime + 0.1); music.tick(); } };
  const playing = () => elements.filter(e => !e.paused).map(e => e.src.replace(/^m\/|\.ogg$/g, ''));
  return { ctx, music, elements, advance, playing, master: ctx.nodes[0], filter: ctx.nodes[1] };
}
/** Started (the unlock gesture) and stopped again, so the test drives the ticks itself. */
function started() { const s = setup(); s.music.start(); s.music.stop(); return s; }

test('six tracks: a menu theme, four for free play and the delivery run starts with the chase track', () => {
  expect(TRACKS.map(t => t.id).sort()).toEqual(['dnb', 'dubtechno', 'funk', 'lofi', 'menu', 'synthwave']);
  expect(PLAYLISTS).toEqual({ menu: ['menu'], explore: ['synthwave', 'lofi', 'funk', 'dubtechno'], mission: ['dnb', 'funk'] });
  for (const list of Object.values(PLAYLISTS)) for (const id of list) expect(TRACKS.some(t => t.id === id)).toBe(true);
});

test('nothing streams before the gesture; a track starts once its request has stood for the settle time', () => {
  const { music, elements, advance, playing, ctx } = setup();
  music.setState('explore'); advance(10);
  expect(elements).toHaveLength(0); // no element, no request: nothing can download
  music.start(); music.stop();
  advance(SETTLE - 0.15); expect(elements).toHaveLength(0);
  advance(0.3);
  expect(playing()).toEqual(['synthwave']); expect(elements[0].src).toBe('m/synthwave.ogg'); expect(elements[0].preload).toBe('none');
  expect(elements[0].preservesPitch).toBe(true); expect(elements[0].loop).toBe(false);
  expect(music.snapshot()).toMatchObject({ state: 'explore', track: { id: 'synthwave', name: 'Aura Neon' }, playing: true, streams: ['synthwave'] });
  // A suspended context (a background tab) schedules nothing.
  ctx.state = 'suspended'; music.setState('menu'); advance(5); expect(playing()).toEqual(['synthwave']);
  music.dispose(); expect(playing()).toEqual([]);
});

test('pause and resume: the menu theme crossfades in after the settle time and free play resumes where it was', () => {
  const { music, elements, advance, playing, ctx } = started();
  music.setState('explore'); advance(1);
  elements[0].currentTime = 37;
  // A quick tap on pause changes nothing.
  music.setState('menu'); advance(0.3); music.setState('explore'); advance(2);
  expect(playing()).toEqual(['synthwave']); expect(elements).toHaveLength(1);
  music.setState('menu'); advance(SETTLE + 0.2);
  expect(playing().sort()).toEqual(['menu', 'synthwave']); // crossfading
  const menu = elements[1]; expect(menu.loop).toBe(true);
  expect(ctx.nodes.at(-2)!.gain.events.at(-1)).toMatchObject({ type: 'ramp', value: 1 }); // the theme's fade gain (the last node is its source)
  advance(2.2); expect(playing()).toEqual(['menu']); expect(music.snapshot().track?.id).toBe('menu');
  music.setState('explore'); advance(SETTLE + 2.4);
  expect(playing()).toEqual(['synthwave']); expect(elements[0].currentTime).toBe(37); expect(elements).toHaveLength(2);
});

test('free play moves on when a track ends, on N and after a delivery; the menu theme loops', () => {
  const { music, elements, advance, playing } = started();
  music.setState('explore'); advance(1);
  elements[0].fire('ended'); expect(playing()).toEqual(['lofi']); // at once, no gap
  music.nextTrack(); advance(0.1); expect(music.snapshot().wanted).toBe('funk'); advance(2.2); expect(playing()).toEqual(['funk']);
  music.nextTrack(); music.nextTrack(); advance(2.3); expect(playing()).toEqual(['synthwave']); // around the playlist
  expect(elements[0].currentTime).toBe(0); // it had ended: from the top again
  music.selectTrack(3); advance(2.3); expect(playing()).toEqual(['dubtechno']);
  music.selectTrack(9); advance(1); expect(playing()).toEqual(['dubtechno']);
});

test('a delivery run: the chase track from the top with a bell, the deadline lifts tempo, brightness and level, then the cadence', () => {
  const { ctx, music, elements, advance, playing, master, filter } = started();
  music.setState('explore'); advance(1);
  const before = ctx.oscillators.length;
  music.setState('mission'); advance(SETTLE + 0.2);
  expect(playing().sort()).toEqual(['dnb', 'synthwave']);
  expect(ctx.oscillators.length).toBe(before + 2); // the mission bell: two notes
  advance(2.2); expect(playing()).toEqual(['dnb']);
  const dnb = elements.find(e => e.src.includes('dnb'))!;
  // Before the hurry starts: the track as it is, a little darker than free play.
  music.setDeadline(0.5); advance(0.3);
  expect(dnb.playbackRate).toBe(1); expect(filter.frequency.value).toBe(12000); expect(music.snapshot().intensity).toBe(0);
  music.setDeadline(0.85); advance(0.3);
  expect(dnb.playbackRate).toBeGreaterThan(1.02); expect(dnb.playbackRate).toBeLessThan(1.06);
  const stingers = ctx.oscillators.length;
  music.setDeadline(1); advance(6);
  expect(dnb.playbackRate).toBeCloseTo(1.06, 5); expect(filter.frequency.value).toBe(18000);
  expect(master.gain.value).toBeCloseTo(0.42 * 1.25, 5);
  expect(ctx.oscillators.length - stingers).toBeGreaterThanOrEqual(4); // a two-note stinger every two seconds
  expect(ctx.oscillators.length - stingers).toBeLessThanOrEqual(6);
  // The track ends mid-run: the funk takes over at the same lifted tempo.
  dnb.fire('ended'); expect(playing()).toEqual(['funk']); expect(elements.find(e => e.src.includes('funk'))!.playbackRate).toBeCloseTo(1.06, 5);
  // Delivered: the resolved cadence (three notes) and free play moves on to its next track at normal speed.
  const cadence = ctx.oscillators.length;
  music.deliver(); advance(0.2);
  expect(ctx.oscillators.length).toBe(cadence + 3); expect(music.snapshot()).toMatchObject({ state: 'explore', wanted: 'lofi', rate: 1 });
  advance(2.2); expect(playing()).toEqual(['lofi']); expect(master.gain.value).toBeCloseTo(0.42, 5);
  // The next run starts the chase track from the top again.
  dnb.currentTime = 80; music.setState('mission'); advance(SETTLE + 2.4);
  expect(playing()).toEqual(['dnb']); expect(dnb.currentTime).toBe(0);
});

test('hurry curve: nothing before its start, then a smooth rise to the full lift', () => {
  const a = hurry(0.5, 0.65, 0.06), b = hurry(0.8, 0.65, 0.06), c = hurry(1, 0.65, 0.06);
  expect(a).toEqual({ intensity: 0, rate: 1, cutoff: 12000, gain: 1 });
  expect(b.rate).toBeGreaterThan(1); expect(b.rate).toBeLessThan(c.rate); expect(c.rate).toBeCloseTo(1.06);
  expect(c.cutoff).toBe(18000); expect(c.gain).toBe(1.25); expect(hurry(NaN, 0.65, 0.06).intensity).toBe(0);
});

test('volume and mute: zero volume fades the stream out and pauses it; muffle lowers the cutoff and the level', () => {
  const { music, elements, advance, playing, master, filter } = started();
  music.setState('explore'); advance(1); expect(master.gain.value).toBeCloseTo(0.42, 5);
  music.setVolume(0.8); expect(master.gain.value).toBeCloseTo(0.8, 5);
  music.setParameters({ muffleCutoff: 750, muffleMs: 600 }); music.setMuffled(0.8);
  expect(filter.frequency.events.at(-1)!.value).toBeCloseTo(4200); expect(filter.frequency.events.at(-1)!.constant).toBeCloseTo(0.2);
  expect(master.gain.value).toBeCloseTo(0.8 * (1 - 0.8 * 0.18), 5);
  music.setVolume(0); advance(SETTLE + 2.4);
  expect(playing()).toEqual([]); expect(music.snapshot()).toMatchObject({ wanted: null, playing: false });
  elements[0].currentTime = 12; music.setVolume(0.5); advance(SETTLE + 0.3);
  expect(playing()).toEqual(['synthwave']); expect(elements[0].currentTime).toBe(12); // unmuted: on from where it stopped
});

test('load guards: held while the world loads, and the start menu theme only for a player who stays', () => {
  // Clicking straight through the start menu: the lobby wait has not passed, then the game holds while it loads.
  const quick = setup();
  quick.music.setState('menu'); quick.music.setLobby(true);
  quick.music.start(); quick.music.stop();
  quick.advance(LOBBY_SETTLE - 0.5); expect(quick.elements).toHaveLength(0);
  quick.music.setLobby(false); quick.music.setHold(true); quick.music.setState('explore');
  quick.advance(20); expect(quick.elements).toHaveLength(0); // nothing requested while loading
  quick.music.setHold(false); quick.advance(SETTLE - 0.2); expect(quick.elements).toHaveLength(0);
  quick.advance(0.4); expect(quick.playing()).toEqual(['synthwave']);
  // Staying in the start menu with audio on: the theme starts after the lobby wait, fades out when the game starts
  // loading, and free play starts when the hold ends.
  const stay = setup();
  stay.music.setState('menu'); stay.music.setLobby(true); stay.music.start(); stay.music.stop();
  stay.advance(LOBBY_SETTLE + 0.2); expect(stay.playing()).toEqual(['menu']);
  stay.music.setLobby(false); stay.music.setHold(true); stay.music.setState('explore');
  stay.advance(SETTLE + 2.4); expect(stay.playing()).toEqual([]); expect(stay.elements).toHaveLength(1);
  stay.music.setHold(false); stay.advance(SETTLE + 0.3); expect(stay.playing()).toEqual(['synthwave']);
});

test('a stream that fails is skipped after a pause; a refused start is retried', async () => {
  const { music, elements, advance, playing } = started();
  music.setState('explore'); advance(1);
  elements[0].fire('error');
  expect(music.snapshot().error).toContain('synthwave'); advance(4); expect(elements).toHaveLength(1);
  advance(2); expect(playing()).toEqual(['lofi']);
  const refuse = started();
  let refused = 0;
  const original = FakeElement.prototype.play;
  FakeElement.prototype.play = async function () { refused++; throw new Error('NotAllowedError'); };
  try { refuse.music.setState('explore'); refuse.advance(1); await Promise.resolve(); await Promise.resolve(); } finally { FakeElement.prototype.play = original; }
  expect(refused).toBe(1); expect(refuse.music.snapshot()).toMatchObject({ track: null }); expect(refuse.music.snapshot().error).toContain('NotAllowedError');
  refuse.advance(6.5); expect(refuse.playing()).toEqual(['synthwave']);
});

test('without media elements (no Audio, as in Node) the music stays silent and the chimes still work', () => {
  const ctx = new FakeContext(), music = new MusicSystem(ctx as unknown as AudioContext);
  music.start(); music.stop(); music.setState('mission');
  for (let i = 0; i < 50; i++) { ctx.currentTime += 0.1; music.tick(); }
  expect(music.snapshot()).toMatchObject({ track: null, playing: false, streams: [] });
  music.deliver(); expect(ctx.oscillators.length).toBe(3);
  music.dispose();
});
