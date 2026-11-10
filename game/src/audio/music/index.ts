import tunedParameters from './params.json';
import { PLAYLISTS, trackOf, type Track } from './tracks';
export { PLAYLISTS, TRACKS, type Track } from './tracks';

export type MusicState = 'explore' | 'mission' | 'menu';
export interface MusicParameters {
  volume: number; crossfadeSeconds: number; muffleCutoff: number; muffleMs: number;
  /** Delivery progress (0..1) at which the hurry starts, the playback-rate lift at the deadline (pitch kept), and the
   *  progress from which the deadline stinger sounds. */
  hurryStart: number; rateLift: number; stingerFrom: number;
}
export const DEFAULT_PARAMETERS: MusicParameters = tunedParameters;

const clamp = (n: number, min = 0, max = 1) => Math.max(min, Math.min(max, Number.isFinite(n) ? n : min));

/** The delivery deadline as music: from `start` the track speeds up (to 1 + lift at the deadline, pitch preserved),
 *  brightens and gets a little louder, smoothly. */
export function hurry(progress: number, start: number, lift: number) {
  const s = clamp(start, 0, 0.95), x = clamp((clamp(progress) - s) / (1 - s)), intensity = x * x * (3 - 2 * x);
  return { intensity, rate: 1 + lift * intensity, cutoff: 12000 + 6000 * intensity, gain: 1 + 0.25 * intensity };
}

/** The part of HTMLAudioElement the music uses (tests pass fakes). */
export interface MusicElement {
  src: string; preload: string; loop: boolean; currentTime: number; playbackRate: number; preservesPitch?: boolean;
  readonly paused: boolean; readonly ended: boolean;
  play(): Promise<void>; pause(): void;
  addEventListener(type: 'ended' | 'error', listener: () => void): void;
}
export interface MusicOptions {
  /** URL prefix of the track files. */
  base?: string;
  /** Makes a media element; the default is `new Audio()` where it exists (without it the music stays silent). */
  element?: () => MusicElement;
}

/** A change of track waits until the request has stood this long: a quick pause and resume, or a click straight
 *  through the start menu, never starts a stream. In the start menu (`setLobby`) the wait is LOBBY_SETTLE. */
export const SETTLE = 0.6, LOBBY_SETTLE = 4;
/** Seconds between deadline stingers. */
const STINGER_EVERY = 2;

interface Voice { track: Track; element: MusicElement; gain: GainNode; node: AudioNode; stopAt: number }

/** Streams the music tracks (one media element per track, made when first played, so nothing downloads before a track
 *  is wanted) through fade gains, a low-pass (muffle, mission brightness) and the volume. The caller starts it inside
 *  a user gesture. */
export class MusicSystem {
  private readonly master: GainNode;
  private readonly filter: BiquadFilterNode;
  private readonly context: AudioContext;
  private readonly base: string;
  private readonly makeElement: (() => MusicElement) | null;
  private interval: ReturnType<typeof setInterval> | null = null;
  private state: MusicState = 'explore';
  /** Position in each state's playlist. */
  private readonly index: Record<MusicState, number> = { menu: 0, explore: 0, mission: 0 };
  private readonly voices = new Map<string, Voice>();
  private current: Voice | null = null;
  private readonly leaving = new Set<Voice>();
  /** Context time of the last change of the wanted track; -Infinity applies it at once. */
  private changedAt = -Infinity;
  private wantedId: string | null = null;
  private deadline = 0;
  private activeMuffle = 0;
  private parameters: MusicParameters = structuredClone(DEFAULT_PARAMETERS);
  private started = false;
  private disposed = false;
  private hold = false;
  private lobby = false;
  private nextStinger = 0;
  private rate = 1;
  private intensity = 0;
  private error: string | null = null;
  private readonly chimes = new Set<{ oscillator: OscillatorNode; gain: GainNode }>();

  constructor(context: AudioContext, destination: AudioNode = context.destination, options: MusicOptions = {}) {
    this.context = context;
    this.base = options.base ?? 'assets/audio/music/';
    this.makeElement = options.element ?? (typeof Audio === 'undefined' || typeof context.createMediaElementSource !== 'function' ? null
      : () => new Audio() as unknown as MusicElement);
    this.master = context.createGain(); this.filter = context.createBiquadFilter();
    this.filter.type = 'lowpass';
    this.master.connect(this.filter); this.filter.connect(destination);
    this.applyTone();
  }

  start() {
    if (this.disposed) return;
    // Resume is initiated synchronously so the browser's activation is retained.
    void this.context.resume();
    if (!this.started) { this.started = true; this.changedAt = this.context.currentTime; }
    if (!this.interval) this.interval = setInterval(() => this.tick(), 100);
    this.tick();
  }
  stop() { if (this.interval) clearInterval(this.interval); this.interval = null; }
  dispose() {
    this.stop(); this.disposed = true;
    for (const voice of this.voices.values()) { voice.element.pause(); voice.node.disconnect(); voice.gain.disconnect(); }
    for (const chime of this.chimes) { chime.oscillator.disconnect(); chime.gain.disconnect(); }
    this.voices.clear(); this.chimes.clear(); this.leaving.clear(); this.current = null;
    this.master.disconnect(); this.filter.disconnect();
  }

  setState(state: MusicState) {
    if (state === this.state) return;
    // A new delivery run starts its music from the top.
    if (state === 'mission') { this.index.mission = 0; for (const id of PLAYLISTS.mission) this.rewind(id); }
    this.state = state; this.touch(); this.applyTone();
  }
  nextTrack() { this.advance(); this.changedAt = -Infinity; }
  selectTrack(index: number) {
    if (!Number.isInteger(index) || index < 0 || index >= PLAYLISTS[this.state].length) return;
    this.index[this.state] = index; this.changedAt = -Infinity;
  }
  setDeadline(progress: number) { this.deadline = clamp(progress); }
  /** A finished delivery resolves the cadence and moves free play on to the next track. */
  deliver() {
    this.chime(this.context.currentTime + 0.02, true); this.deadline = 0;
    this.index.explore = (this.index.explore + 1) % PLAYLISTS.explore.length;
    this.state = 'explore'; this.changedAt = -Infinity; this.applyTone();
  }
  /** Called every frame (play.ts); only a changed amount reschedules the filter and gain. */
  setMuffled(amount: number) { const next = clamp(amount); if (next === this.activeMuffle) return; this.activeMuffle = next; this.applyTone(); }
  setVolume(value: number) { const next = clamp(value); if (next === this.parameters.volume) return; this.parameters.volume = next; this.applyTone(); }
  /** While held (the world is loading) no stream starts; a track that is no longer wanted still fades out. */
  setHold(hold: boolean) { if (hold !== this.hold) { this.hold = hold; this.touch(); } }
  /** The start menu: its theme starts only once the player has stayed for LOBBY_SETTLE seconds with audio on, so
   *  clicking straight through to the game downloads no music. */
  setLobby(lobby: boolean) { if (lobby !== this.lobby) { this.lobby = lobby; this.touch(); } }
  setParameters(patch: Partial<MusicParameters>) { this.parameters = { ...this.parameters, ...patch }; this.applyTone(); }
  getParameters(): MusicParameters { return structuredClone(this.parameters); }

  snapshot() {
    const c = this.current;
    return { state: this.state, track: c ? { id: c.track.id, name: c.track.name, style: c.track.style } : null, wanted: this.wanted()?.id ?? null,
      playing: !!c && !c.element.paused, position: c ? +c.element.currentTime.toFixed(1) : 0, rate: +this.rate.toFixed(3),
      intensity: +this.intensity.toFixed(2), hold: this.hold, lobby: this.lobby, streams: [...this.voices.keys()], fading: this.leaving.size, error: this.error };
  }

  private touch() { if (this.changedAt !== -Infinity || this.started) this.changedAt = Math.max(this.changedAt, this.context.currentTime); }
  private advance(state: MusicState = this.state) { this.index[state] = (this.index[state] + 1) % PLAYLISTS[state].length; }
  private rewind(id: string) { const voice = this.voices.get(id); if (voice && voice !== this.current) voice.element.currentTime = 0; }
  /** The track that should be playing: none while the volume is zero. */
  private wanted(): Track | null {
    return this.parameters.volume > 0 ? trackOf(PLAYLISTS[this.state][this.index[this.state] % PLAYLISTS[this.state].length]) : null;
  }

  private applyTone() {
    const p = this.parameters, amount = this.activeMuffle, t = this.context.currentTime, tau = Math.max(0.001, p.muffleMs / 3000);
    const curve = hurry(this.deadline, p.hurryStart, p.rateLift), mission = this.state === 'mission';
    const open = 18000 * (1 - amount) + p.muffleCutoff * amount;
    this.filter.frequency.setTargetAtTime(Math.min(mission ? curve.cutoff : 18000, open), t, tau);
    this.master.gain.setTargetAtTime(p.volume * (1 - amount * 0.18) * (mission ? curve.gain : 1), t, tau);
  }

  /** Public for deterministic fake-clock tests; the interval calls the same method. */
  tick() {
    if (!this.started || this.disposed || this.context.state !== 'running') return;
    const now = this.context.currentTime, p = this.parameters;
    for (const voice of this.leaving) if (now >= voice.stopAt) { voice.element.pause(); this.leaving.delete(voice); }
    const wanted = this.wanted();
    if ((wanted?.id ?? null) !== this.wantedId) { this.wantedId = wanted?.id ?? null; if (this.changedAt !== -Infinity) this.changedAt = Math.max(this.changedAt, now); }
    if ((this.current?.track.id ?? null) !== this.wantedId) {
      const settled = now - this.changedAt >= (this.lobby ? LOBBY_SETTLE : SETTLE);
      // What plays is no longer wanted: it fades out once the request has settled (also while held).
      if (this.current && settled) { this.leave(this.current, now); this.current = null; }
      if (wanted && settled && !this.hold && this.makeElement) { this.enter(wanted, now); this.changedAt = now; }
    }
    // The delivery deadline: rate, brightness and level follow it; near the end a stinger every two seconds.
    const curve = hurry(this.deadline, p.hurryStart, p.rateLift), mission = this.state === 'mission';
    const rate = mission ? curve.rate : 1, intensity = mission ? curve.intensity : 0;
    if (rate !== this.rate && (Math.abs(rate - this.rate) > 0.002 || rate === 1)) { this.rate = rate; if (this.current) this.current.element.playbackRate = rate; }
    if (Math.abs(intensity - this.intensity) > 0.01 || (intensity === 0) !== (this.intensity === 0)) { this.intensity = intensity; this.applyTone(); }
    if (mission && this.deadline >= p.stingerFrom && now >= this.nextStinger) { this.chime(now, false); this.nextStinger = now + STINGER_EVERY; }
  }

  private voice(track: Track): Voice {
    let voice = this.voices.get(track.id);
    if (!voice) {
      const element = this.makeElement!(), gain = this.context.createGain();
      element.preload = 'none'; element.src = `${this.base}${track.id}.ogg`;
      if ('preservesPitch' in element) element.preservesPitch = true;
      const node = this.context.createMediaElementSource(element as unknown as HTMLMediaElement);
      gain.gain.value = 0;
      node.connect(gain); gain.connect(this.master);
      voice = { track, element, gain, node, stopAt: Infinity };
      this.voices.set(track.id, voice);
      const mine = voice;
      // A track that ends hands over to the next of its playlist at once; a failed one after a pause.
      element.addEventListener('ended', () => { if (this.current === mine) this.finished(mine); });
      element.addEventListener('error', () => {
        this.error = `${track.id}: stream failed`; element.pause(); mine.gain.gain.value = 0;
        if (this.current === mine) { this.current = null; this.advance(); this.changedAt = this.context.currentTime + 5; }
      });
    }
    return voice;
  }

  private enter(track: Track, now: number) {
    const voice = this.voice(track), element = voice.element, fade = this.leaving.size || this.current ? this.parameters.crossfadeSeconds : 0.4;
    this.leaving.delete(voice);
    element.loop = PLAYLISTS.menu.includes(track.id);
    if (element.ended || element.currentTime > track.seconds - 3) element.currentTime = 0;
    element.playbackRate = this.rate;
    voice.gain.gain.cancelScheduledValues?.(now); voice.gain.gain.setValueAtTime(voice.gain.gain.value, now);
    voice.gain.gain.linearRampToValueAtTime(1, now + fade);
    // A refused or failed start is tried again in a few seconds.
    void element.play().catch(error => { this.error = `${track.id}: ${String(error)}`; if (this.current === voice) { this.current = null; this.changedAt = this.context.currentTime + 5; } });
    if (this.state === 'mission' && this.current?.track.id !== track.id && PLAYLISTS.mission[0] === track.id && element.currentTime < 1) this.chime(now, false);
    this.current = voice;
  }

  private leave(voice: Voice, now: number) {
    const fade = this.parameters.crossfadeSeconds;
    voice.gain.gain.cancelScheduledValues?.(now); voice.gain.gain.setValueAtTime(voice.gain.gain.value, now);
    voice.gain.gain.linearRampToValueAtTime(0, now + fade);
    voice.stopAt = now + fade + 0.05;
    this.leaving.add(voice);
  }

  private finished(voice: Voice) {
    const state = (Object.keys(PLAYLISTS) as MusicState[]).find(s => s === this.state && PLAYLISTS[s].includes(voice.track.id));
    voice.gain.gain.value = 0;
    this.current = null;
    if (state) this.advance(state);
    this.changedAt = -Infinity;
    this.tick();
  }

  /** The mission bell (two rising notes), the deadline stinger (the same) and the resolved delivery cadence. */
  private chime(time: number, resolved: boolean) {
    const root = resolved ? 392 : 659.26;
    for (const [i, ratio] of (resolved ? [1, 1.25, 1.5] : [1, 1.2]).entries()) {
      const t = Math.max(this.context.currentTime, time + i * 0.09), oscillator = this.context.createOscillator(), gain = this.context.createGain();
      oscillator.type = 'sine'; oscillator.frequency.setValueAtTime(root * ratio, t);
      gain.gain.setValueAtTime(0, t); gain.gain.linearRampToValueAtTime(0.09, t + 0.01); gain.gain.setTargetAtTime(0, t + 0.07, 0.1);
      oscillator.connect(gain); gain.connect(this.master); oscillator.start(t); oscillator.stop(t + 0.45);
      const chime = { oscillator, gain }; this.chimes.add(chime);
      oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); this.chimes.delete(chime); };
    }
  }
}
