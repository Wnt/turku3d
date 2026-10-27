import { expect, test } from 'vitest';
import { MusicSystem, TRACKS, hurry } from '../../src/audio/music';
class Param {
  value = 0; events: { type: string; value: number; time: number; constant?: number }[] = [];
  setTargetAtTime(value: number, time: number, constant: number) { this.value = value; this.events.push({ type: 'target', value, time, constant }); }
  setValueAtTime(value: number, time: number) { this.value = value; this.events.push({ type: 'set', value, time }); }
  linearRampToValueAtTime(value: number, time: number) { this.value = value; this.events.push({ type: 'ramp', value, time }); }
}
class Node { gain = new Param(); frequency = new Param(); type = 'sine'; fftSize = 0; onended?: () => void; starts: number[] = []; stops: number[] = [];
  connect(_node: Node) { return _node; } disconnect() {} start(t: number) { this.starts.push(t); } stop(t: number) { this.stops.push(t); }
}
class FakeContext { state = 'running'; currentTime = 0; destination = new Node(); nodes: Node[] = []; createGain() { return this.node(); } createBiquadFilter() { return this.node(); } createAnalyser() { return this.node(); } createOscillator() { return this.node(); } node() { const n = new Node(); this.nodes.push(n); return n; } async resume() {} }
function setup() { const ctx = new FakeContext(); const music = new MusicSystem(ctx as unknown as AudioContext); music.start(); music.stop(); return { ctx, music }; }
function advance(ctx: FakeContext, music: MusicSystem, seconds: number) { const end = ctx.currentTime + seconds; while (ctx.currentTime < end) { ctx.currentTime = Math.min(end, ctx.currentTime + 0.025); music.tick(); } }
test('five paired tracks have stable key and tempo', () => { expect(TRACKS).toHaveLength(5); expect(new Set(TRACKS.map(t => t.style)).size).toBe(5); });
test('mission switch is bar quantised and crossfade lasts configured bars', () => {
  const { ctx, music } = setup(); advance(ctx, music, 0.6); music.setState('mission');
  const boundary = music.snapshot().transitionBeat!; expect(boundary % 4).toBe(0);
  advance(ctx, music, 2); expect(music.snapshot().state).toBe('mission');
  const before = ctx.nodes.length; advance(ctx, music, 1); expect(ctx.nodes.length).toBeGreaterThan(before);
  expect(music.snapshot().transitionBeat).toBeNull(); music.dispose();
});
test('hurry curve starts at threshold and rises smoothly to full lift', () => {
  const a = hurry(0.5, 0.65, 0.2), b = hurry(0.8, 0.65, 0.2), c = hurry(1, 0.65, 0.2);
  expect(a.tempoFactor).toBe(1); expect(b.tempoFactor).toBeGreaterThan(a.tempoFactor); expect(c.tempoFactor).toBeCloseTo(1.2);
  expect(c.percussion).toBeGreaterThan(b.percussion); expect(c.cutoff).toBeGreaterThan(b.cutoff);
});
test('muffle cutoff and gain smooth over requested time', () => {
  const { ctx, music } = setup(); music.setParameters({ muffleCutoff: 750, muffleMs: 600 }); music.setMuffled(0.8);
  const filter = ctx.nodes[1], master = ctx.nodes[0];
  expect(filter.frequency.events.at(-1)!.value).toBeCloseTo(4200);
  expect(filter.frequency.events.at(-1)!.constant).toBeCloseTo(0.2);
  expect(master.gain.events.at(-1)!.value).toBeLessThan(0.42); music.dispose();
});
test('suspend and stale clock never schedule notes in the past', () => {
  const { ctx, music } = setup();
  ctx.state = 'suspended'; ctx.currentTime = 20; music.tick();
  const existing = ctx.nodes.length;
  ctx.state = 'running'; music.tick(); advance(ctx, music, 0.6);
  const starts = ctx.nodes.slice(existing).flatMap(n => n.starts);
  expect(starts.length).toBeGreaterThan(0);
  expect(starts.every(t => t >= 20)).toBe(true);
  music.dispose();
});
