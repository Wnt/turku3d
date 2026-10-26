import type { MissionHudSource } from '../gameplay/contract';
import { VOICE_CUT_SECONDS } from './mix';

/** One row of public/assets/voice/manifest.json, written by audio/voice (make voice-build). */
export interface VoiceClip { id: string; speaker: string; file: string; seconds: number; text: string; textSha256: string }
export interface VoiceManifest { version: number; lines: VoiceClip[] }

/** What the player needs from the mix: the running context, the voice bus and ambience ducking. */
export interface VoiceOutput {
  context(): AudioContext | null;
  bus(): AudioNode | null;
  duck(active: boolean): void;
  /** Calls back each time the context starts running (first unlock and later resumes). */
  onRunning(callback: () => void): void;
}

type Playing = { clip: VoiceClip; source: AudioBufferSourceNode; gain: GainNode };

/** Plays the clip whose text equals the HUD message that was just shown. One line at a time: a new voiced
 *  line fades the previous one out over VOICE_CUT_SECONDS and starts after it. Unvoiced messages (prompts
 *  without a clip) and repeated states with the same message leave the current line alone. A line shown
 *  before the audio context runs waits for unlock, unless another message replaces it first. */
export class VoicePlayer {
  private clips = new Map<string, VoiceClip>();
  private buffers = new Map<string, Promise<AudioBuffer>>();
  private shown: string | null = null;
  private pending: VoiceClip | null = null;
  private playing: Playing | null = null;
  private queue: string[] = [];
  private request = 0;
  private unsubscribe: (() => void) | null = null;
  private disposed = false;
  readonly ready: Promise<void>;
  readonly played: string[] = [];
  readonly errors: string[] = [];

  constructor(private readonly output: VoiceOutput,
    private readonly fetcher: typeof fetch = (...args) => fetch(...args),
    private readonly base = `${import.meta.env.BASE_URL}assets/voice/`) {
    this.ready = this.fetcher(`${base}manifest.json`)
      .then(response => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); })
      .then((manifest: VoiceManifest) => {
        for (const clip of manifest.lines) this.clips.set(clip.text, clip);
        if (this.shown !== null) this.show(this.shown, true);
        if (!this.playing) this.advance();
      })
      .catch(error => { this.errors.push(`manifest: ${String(error)}`); });
    output.onRunning(() => {
      const clip = this.pending;
      this.pending = null;
      if (clip) void this.start(clip);
      else if (!this.playing) this.advance();
    });
  }

  /** Follows a mission HUD source; attaching another source replaces the previous subscription. */
  attach(source: MissionHudSource) {
    this.unsubscribe?.();
    this.unsubscribe = source.subscribe(state => this.show(state.message));
  }

  /** The HUD now shows `message`. Exposed for the start screen and diagnostics. */
  show(message: string | null, force = false) {
    if (this.disposed || (message === this.shown && !force)) return;
    this.shown = message;
    this.pending = null; // a line waiting for unlock only plays while it is still the shown message
    const clip = message === null ? undefined : this.clips.get(message);
    if (clip) { this.queue = []; void this.start(clip); }
  }

  /** Speaks texts one after another (the start screen's story intro). A newly shown voiced HUD message
   *  cuts the narration; unvoiced messages do not. Waits for the manifest and for unlock. */
  narrate(texts: readonly string[]) {
    if (this.disposed) return;
    this.queue = [...texts];
    if (!this.playing) this.advance();
  }

  /** Starts the next narration clip; false when nothing could start (empty queue, locked or no manifest). */
  private advance() {
    const ctx = this.output.context();
    if (!this.clips.size || !ctx || ctx.state !== 'running') return false;
    while (this.queue.length) {
      const clip = this.clips.get(this.queue.shift()!);
      if (clip) { void this.start(clip); return true; }
    }
    return false;
  }

  private load(clip: VoiceClip, ctx: AudioContext) {
    let buffer = this.buffers.get(clip.id);
    if (!buffer) {
      buffer = this.fetcher(`${this.base}${clip.file}`).then(response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.arrayBuffer();
      }).then(bytes => ctx.decodeAudioData(bytes));
      this.buffers.set(clip.id, buffer);
      buffer.catch(() => this.buffers.delete(clip.id));
    }
    return buffer;
  }

  private async start(clip: VoiceClip) {
    const ctx = this.output.context(), bus = this.output.bus();
    if (!ctx || !bus || ctx.state !== 'running') { this.pending = clip; return; }
    const request = ++this.request; // only the newest voiced line may start
    let buffer: AudioBuffer;
    try { buffer = await this.load(clip, ctx); } catch (error) { this.errors.push(`${clip.id}: ${String(error)}`); return; }
    if (request !== this.request || this.disposed) return;
    let at = ctx.currentTime;
    if (this.playing) { this.cut(this.playing, at); at += VOICE_CUT_SECONDS; }
    const source = ctx.createBufferSource(), gain = ctx.createGain();
    source.buffer = buffer;
    source.connect(gain); gain.connect(bus);
    const playing: Playing = { clip, source, gain };
    source.onended = () => {
      source.disconnect(); gain.disconnect();
      if (this.playing !== playing) return;
      this.playing = null;
      if (!this.advance()) this.output.duck(false);
    };
    this.playing = playing;
    this.output.duck(true);
    source.start(at);
    this.played.push(clip.id);
  }

  private cut(playing: Playing, at: number) {
    const gain = playing.gain.gain;
    gain.cancelScheduledValues(at);
    gain.setValueAtTime(gain.value, at);
    gain.linearRampToValueAtTime(0, at + VOICE_CUT_SECONDS);
    playing.source.stop(at + VOICE_CUT_SECONDS);
  }

  snapshot() {
    return { clips: this.clips.size, shown: this.shown, playing: this.playing?.clip.id ?? null,
      pending: this.pending?.id ?? null, queued: this.queue.length, played: [...this.played], errors: [...this.errors] };
  }

  dispose() {
    this.disposed = true;
    this.queue = [];
    this.unsubscribe?.();
    if (this.playing) { this.playing.source.stop(); this.playing = null; }
  }
}
