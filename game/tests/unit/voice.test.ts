import { expect, test } from 'vitest';
import { GameAudio } from '../../src/audio';
import { duckGain, VOICE_CUT_SECONDS } from '../../src/audio/mix';
import { VoicePlayer, type VoiceManifest, type VoiceOutput } from '../../src/audio/voice';
import { EMPTY_HUD_STATE, type HudStateListener, type MissionHudSource } from '../../src/gameplay/contract';

class Param {
  value = 1;
  events: [string, number, number][] = [];
  setTargetAtTime(value: number) { this.value = value; }
  cancelScheduledValues(at: number) { this.events.push(['cancel', 0, at]); }
  setValueAtTime(value: number, at: number) { this.events.push(['set', value, at]); }
  linearRampToValueAtTime(value: number, at: number) { this.events.push(['ramp', value, at]); this.value = value; }
}
class Node {
  gain = new Param(); playbackRate = new Param(); threshold = new Param(); ratio = new Param();
  buffer: unknown; startedAt: number | null = null; stoppedAt: number | null = null;
  onended?: () => void;
  connections: Node[] = [];
  connect(node: Node) { this.connections.push(node); }
  disconnect() {}
  start(at = 0) { this.startedAt = at; }
  stop(at = 0) { this.stoppedAt = at; }
  end() { this.onended?.(); }
}
class FakeContext {
  state = 'suspended'; currentTime = 0; destination = new Node(); sources: Node[] = []; nodes: Node[] = [];
  listener = {};
  createGain() { return this.node(); }
  createDynamicsCompressor() { return this.node(); }
  createBufferSource() { const node = this.node(); this.sources.push(node); return node; }
  node() { const node = new Node(); this.nodes.push(node); return node; }
  async resume() { this.state = 'running'; }
  async close() { this.state = 'closed'; }
  async decodeAudioData(bytes: ArrayBuffer) { return { bytes: bytes.byteLength }; }
}

const MANIFEST: VoiceManifest = { version: 1, lines: [
  { id: 'market_offer', speaker: 'market', file: 'market_offer.ogg', seconds: 2.5, text: 'A licence? Eighty euros.', textSha256: 'a' },
  { id: 'market_sold', speaker: 'market', file: 'market_sold.ogg', seconds: 3, text: "It's yours now.", textSha256: 'b' },
] };
const OFFER = MANIFEST.lines[0].text, SOLD = MANIFEST.lines[1].text, PROMPT = 'Press E to buy a work licence for 80 €.';

function fakeFetch(fetched: string[], manifest: VoiceManifest = MANIFEST) {
  return (async (url: string) => {
    fetched.push(url);
    return { ok: true, json: async () => manifest, arrayBuffer: async () => new ArrayBuffer(8) };
  }) as unknown as typeof fetch;
}

function rig() {
  const ctx = new FakeContext(), bus = new Node(), ducks: boolean[] = [], running: (() => void)[] = [], fetched: string[] = [];
  const output: VoiceOutput = {
    context: () => ctx as unknown as AudioContext, bus: () => bus as unknown as AudioNode,
    duck: active => { ducks.push(active); }, onRunning: callback => { running.push(callback); },
  };
  const player = new VoicePlayer(output, fakeFetch(fetched), '/v/');
  const unlock = async () => { ctx.state = 'running'; running.forEach(callback => callback()); await settle(); };
  return { ctx, bus, ducks, fetched, player, unlock };
}
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

function hud() {
  let listener: HudStateListener | null = null, unsubscribed = 0;
  const source: MissionHudSource = { subscribe(l) { listener = l; l(EMPTY_HUD_STATE); return () => { unsubscribed++; }; } };
  return { source, set: (message: string | null) => listener!({ ...EMPTY_HUD_STATE, message }), unsubscribed: () => unsubscribed };
}

test('plays the clip whose text equals the shown message, once per change', async () => {
  const { ctx, bus, ducks, fetched, player, unlock } = rig();
  await unlock(); await player.ready;
  const { source, set } = hud();
  player.attach(source);
  set(OFFER); await settle();
  expect(ctx.sources).toHaveLength(1);
  expect(ctx.sources[0].startedAt).toBe(0);
  expect(ctx.sources[0].connections[0].connections).toContain(bus);
  expect(fetched).toEqual(['/v/manifest.json', '/v/market_offer.ogg']);
  expect(ducks).toEqual([true]);
  set(OFFER); await settle(); // timer ticks resend the same message
  expect(ctx.sources).toHaveLength(1);
  set(PROMPT); await settle(); // unvoiced prompt: the line keeps playing
  expect(ctx.sources[0].stoppedAt).toBeNull();
  ctx.sources[0].end();
  expect(ducks).toEqual([true, false]);
  set(null); set(OFFER); await settle(); // shown again later: plays again, buffer reused
  expect(ctx.sources).toHaveLength(2);
  expect(fetched.filter(url => url.endsWith('market_offer.ogg'))).toHaveLength(1);
  expect(player.snapshot()).toMatchObject({ clips: 2, playing: 'market_offer', queued: 0, played: ['market_offer', 'market_offer'], errors: [] });
});

test('a new line fades the previous out over 100 ms and starts after it; ducking spans both', async () => {
  const { ctx, ducks, player, unlock } = rig();
  await unlock(); await player.ready;
  player.show(OFFER); await settle();
  ctx.currentTime = 1.25;
  player.show(SOLD); await settle();
  const [first, second] = ctx.sources;
  const fade = first.connections[0].gain.events;
  expect(fade).toEqual([['cancel', 0, 1.25], ['set', 1, 1.25], ['ramp', 0, 1.25 + VOICE_CUT_SECONDS]]);
  expect(first.stoppedAt).toBeCloseTo(1.35);
  expect(second.startedAt).toBeCloseTo(1.35);
  first.end(); // the cut line ending must not release the duck held by the new line
  expect(ducks).toEqual([true, true]);
  second.end();
  expect(ducks).toEqual([true, true, false]);
});

test('lines shown before unlock wait for it, unless replaced first', async () => {
  const { ctx, player, unlock } = rig();
  await player.ready;
  player.show(OFFER); await settle();
  expect(ctx.sources).toHaveLength(0);
  expect(player.snapshot().pending).toBe('market_offer');
  await unlock();
  expect(ctx.sources).toHaveLength(1);

  const late = rig();
  await late.player.ready;
  late.player.show(OFFER); late.player.show(PROMPT);
  await late.unlock();
  expect(late.ctx.sources).toHaveLength(0);
});

test('only the newest of overlapping requests starts; a message shown before the manifest still plays', async () => {
  const { ctx, player, unlock } = rig();
  player.show(OFFER); // manifest still loading
  await unlock(); await player.ready; await settle();
  expect(player.snapshot().played).toEqual(['market_offer']);
  ctx.sources[0].end();
  player.show(SOLD); player.show(OFFER); // both decode concurrently
  await settle(); await settle();
  expect(player.snapshot().played).toEqual(['market_offer', 'market_offer']);
});

test('narration plays in order, waits for unlock, survives prompts and is cut by a voiced line', async () => {
  const { ctx, ducks, player, unlock } = rig();
  player.narrate([OFFER, 'Not in the manifest.', SOLD, OFFER]);
  await player.ready;
  expect(ctx.sources).toHaveLength(0);
  await unlock();
  expect(player.snapshot()).toMatchObject({ playing: 'market_offer', queued: 3 });
  player.show(PROMPT); // unvoiced: narration continues
  ctx.sources[0].end(); await settle();
  expect(player.snapshot()).toMatchObject({ playing: 'market_sold', queued: 1 });
  expect(ducks).toEqual([true, true]); // stays ducked between narration lines
  player.show(SOLD); await settle(); // a voiced HUD line cuts the narration and clears the queue
  expect(ctx.sources[1].stoppedAt).not.toBeNull();
  ctx.sources[1].end(); ctx.sources[2].end(); await settle();
  expect(player.snapshot()).toMatchObject({ playing: null, queued: 0, played: ['market_offer', 'market_sold', 'market_sold'] });
  expect(ducks.at(-1)).toBe(false);
});

test('dialog callback follows the natural clip end and manual advance cancels it', async () => {
  const { ctx, player, unlock } = rig();
  await unlock(); await player.ready;
  const ended: string[] = [];
  player.playDialogLine(OFFER, () => ended.push('first'));
  await settle();
  expect(player.snapshot().playing).toBe('market_offer');
  player.stopDialogLine();
  ctx.sources[0].end();
  expect(ended).toEqual([]);
  player.playDialogLine(SOLD, () => ended.push('second'));
  await settle();
  ctx.sources[1].end();
  expect(ended).toEqual(['second']);
  expect(player.snapshot().playing).toBeNull();
});

test('attach replaces the subscription; missing manifest is contained', async () => {
  const { player } = rig();
  const a = hud(), b = hud();
  player.attach(a.source); player.attach(b.source);
  expect(a.unsubscribed()).toBe(1);
  player.dispose();
  expect(b.unsubscribed()).toBe(1);

  const broken = new VoicePlayer({ context: () => null, bus: () => null, duck: () => {}, onRunning: () => {} },
    (async () => ({ ok: false, status: 404 })) as unknown as typeof fetch, '/v/');
  await broken.ready;
  broken.show(OFFER);
  expect(broken.snapshot()).toMatchObject({ clips: 0, playing: null, errors: ['manifest: Error: HTTP 404'] });
});

test('GameAudio routes voice to its own bus and ducks ambience by 6 dB without touching its volume', async () => {
  const ctx = new FakeContext();
  const audio = new GameAudio(() => ctx as unknown as AudioContext,
    (async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(4) })) as unknown as typeof fetch);
  const output = audio.voiceOutput();
  expect(output.context()).toBeNull();
  let ran = 0;
  output.onRunning(() => { ran++; });
  await audio.unlock();
  expect(ran).toBe(1);
  const [master, , ducker] = ctx.nodes;
  const ambienceBus = ctx.nodes.find(node => node.connections.includes(ducker))!;
  expect(output.bus()).not.toBe(ambienceBus);
  expect((output.bus() as unknown as Node).connections).toContain(master);
  expect(audio.volumes.voice).toBe(0.9);
  output.duck(true);
  expect(ducker.gain.value).toBeCloseTo(duckGain(true));
  expect(20 * Math.log10(duckGain(true))).toBeCloseTo(-6);
  expect(ambienceBus.gain.value).toBe(audio.volumes.ambience);
  expect(audio.snapshot().ducked).toBe(true);
  output.duck(false);
  expect(ducker.gain.value).toBe(1);
  audio.dispose();
  expect(output.context()).toBeNull();
});
