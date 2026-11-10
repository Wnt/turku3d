import { expect, test } from 'vitest';
import { ENTRY_QUIET, GameAudio } from '../../src/audio';

class Param { value = 0; setTargetAtTime(v: number) { this.value = v; } setValueAtTime(v: number) { this.value = v; } linearRampToValueAtTime(v: number) { this.value = v; } cancelScheduledValues() {} }
class FakeNode {
  gain = new Param(); frequency = new Param(); Q = new Param(); threshold = new Param(); ratio = new Param(); playbackRate = new Param();
  positionX = new Param(); positionY = new Param(); positionZ = new Param();
  type = ''; buffer: unknown; loop = false; onended?: () => void;
  connect(n: unknown) { return n; } disconnect() {} start() {} stop() {}
}
const requested: string[] = [];
class FakeAudio {
  preload = ''; loop = false; currentTime = 0; playbackRate = 1; preservesPitch = false; paused = true; ended = false;
  private url = '';
  get src() { return this.url; } set src(value: string) { this.url = value; }
  async play() { if (this.paused) requested.push(this.url); this.paused = false; } // a stream opens when it first plays
  pause() { this.paused = true; }
  addEventListener() {}
}

test('GameAudio: music streams on demand after the game is playable, follows the states, and the volume and mute controls hold', async () => {
  const g = globalThis as unknown as { Audio?: unknown; localStorage?: unknown };
  const before = g.Audio; g.Audio = FakeAudio; requested.length = 0;
  try {
    const make = () => new FakeNode();
    const ctx = { state: 'suspended', currentTime: 0, destination: new FakeNode(), listener: { setPosition() {}, setOrientation() {} },
      createGain: make, createDynamicsCompressor: make, createBufferSource: make, createPanner: make, createBiquadFilter: make, createAnalyser: make,
      createOscillator: make, createMediaElementSource: make,
      resume: async () => { ctx.state = 'running'; }, close: async () => { ctx.state = 'closed'; }, decodeAudioData: async () => ({}) };
    const fetched: string[] = [];
    const fetcher = (async (url: string) => { fetched.push(url); return { ok: true, arrayBuffer: async () => new ArrayBuffer(4) }; }) as unknown as typeof fetch;
    let now = 0;
    const audio = new GameAudio(() => ctx as unknown as AudioContext, fetcher, 'audio/', () => now);
    // A frame of the play session: the guards, then the music state; the context clock runs with the game clock.
    const frame = (seconds: number, loading: boolean, lobby: boolean, state: 'menu' | 'explore' | 'mission') => {
      for (const end = now + seconds; now < end - 1e-9; ) { now += 0.1; ctx.currentTime = now; audio.setLoading(loading, lobby); audio.setMusicState(state); audio.music?.tick(); }
    };
    const track = () => audio.snapshot().music.playing?.track?.id ?? null;
    // The start menu before any gesture: effects preload, no music.
    await audio.preload(); frame(30, false, true, 'menu');
    expect(fetched.some(url => url.includes('music'))).toBe(false); expect(requested).toEqual([]);
    // The click that starts the game unlocks audio; the menu closes at once: no menu theme, and no stream for the
    // first fifteen seconds of play.
    await audio.unlock(); audio.music?.stop();
    frame(0.2, false, true, 'menu'); frame(ENTRY_QUIET - 0.2, false, false, 'explore');
    expect(requested).toEqual([]); expect(track()).toBeNull();
    frame(1.2, false, false, 'explore');
    expect(requested).toEqual(['audio/music/synthwave.ogg']); expect(track()).toBe('synthwave');
    expect(audio.snapshot().music).toMatchObject({ state: 'explore', volume: 0.42, muted: false, playing: { playing: true, hold: false } });
    // Pausing brings the menu theme; a delivery brings the chase track; the deadline reaches the stream's rate.
    frame(3.5, false, false, 'menu'); expect(track()).toBe('menu');
    frame(3.5, false, false, 'explore'); expect(track()).toBe('synthwave');
    frame(3.5, false, false, 'mission'); expect(track()).toBe('dnb');
    audio.setMusicDeadline(1); frame(0.3, false, false, 'mission');
    expect(audio.snapshot().music).toMatchObject({ deadline: 1, playing: { rate: 1.06, intensity: 1 } });
    audio.resolveDelivery(); frame(3, false, false, 'explore');
    expect(track()).toBe('lofi'); expect(audio.snapshot().music.playing?.rate).toBe(1);
    audio.nextTrack(); frame(3, false, false, 'explore'); expect(track()).toBe('funk');
    // Mute pauses the stream and unmute resumes it; the volume slider reaches the master gain; both are remembered.
    audio.setMusicMuted(true); frame(3.5, false, false, 'explore');
    expect(audio.snapshot().music).toMatchObject({ muted: true, playing: { playing: false, wanted: null } });
    audio.setMusicMuted(false); audio.setMusicVolume(0.7); frame(1.5, false, false, 'explore');
    expect(track()).toBe('funk'); expect(audio.snapshot().music).toMatchObject({ volume: 0.7, muted: false, playing: { playing: true } });
    // Loading a save: what plays fades out, nothing starts until fifteen seconds after it ends.
    const streams = requested.length;
    frame(6, true, false, 'menu'); expect(audio.snapshot().music.playing).toMatchObject({ playing: false, hold: true });
    frame(ENTRY_QUIET - 0.5, false, false, 'explore'); expect(track()).toBeNull();
    frame(1.5, false, false, 'explore'); expect(track()).toBe('funk'); expect(requested.length).toBe(streams + 1);
    // Six files at most are ever opened, each only when first played.
    expect(new Set(requested).size).toBeLessThanOrEqual(6);
    expect(audio.errors).toEqual([]);
    audio.dispose();
  } finally { g.Audio = before; }
});

test('GameAudio: the start menu theme plays for a player who stays in the menu with audio on', async () => {
  const g = globalThis as unknown as { Audio?: unknown };
  const before = g.Audio; g.Audio = FakeAudio; requested.length = 0;
  try {
    const make = () => new FakeNode();
    const ctx = { state: 'suspended', currentTime: 0, destination: new FakeNode(), listener: { setPosition() {}, setOrientation() {} },
      createGain: make, createDynamicsCompressor: make, createBufferSource: make, createPanner: make, createBiquadFilter: make, createAnalyser: make,
      createOscillator: make, createMediaElementSource: make,
      resume: async () => { ctx.state = 'running'; }, close: async () => { ctx.state = 'closed'; }, decodeAudioData: async () => ({}) };
    let now = 0;
    const audio = new GameAudio(() => ctx as unknown as AudioContext, (async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(4) })) as unknown as typeof fetch, 'audio/', () => now);
    const frame = (seconds: number, lobby: boolean, state: 'menu' | 'explore') => {
      for (const end = now + seconds; now < end - 1e-9; ) { now += 0.1; ctx.currentTime = now; audio.setLoading(false, lobby); audio.setMusicState(state); audio.music?.tick(); }
    };
    frame(5, true, 'menu');
    await audio.unlock(); audio.music?.stop(); // a key press in the menu
    frame(3.5, true, 'menu'); expect(requested).toEqual([]);
    frame(1, true, 'menu'); expect(requested).toEqual(['audio/music/menu.ogg']);
    expect(audio.snapshot().music.playing).toMatchObject({ track: { id: 'menu', name: 'Harbour Glass' }, lobby: true });
    // Starting the game: the theme fades out, free play follows fifteen seconds in.
    frame(3, false, 'explore'); expect(audio.snapshot().music.playing?.playing).toBe(false);
    frame(ENTRY_QUIET - 3.2, false, 'explore'); expect(audio.snapshot().music.playing?.track).toBeNull();
    frame(1.2, false, 'explore'); expect(audio.snapshot().music.playing?.track?.id).toBe('synthwave');
    audio.dispose();
  } finally { g.Audio = before; }
});
