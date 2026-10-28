import { loading } from '../core/loading';
import type { MissionHudSource } from '../gameplay/contract';
import { VOICE_CUT_SECONDS } from './mix';

/** One row of public/assets/voice/manifest.json, written by audio/voice (make voice-build). */
export interface VoiceClip { id: string; speaker: string; file: string; seconds: number; text: string; textSha256: string }
export interface VoiceManifest { version: number; lines: VoiceClip[] }

/** Resolve an address announcement to clips in speaking order, using only committed manifest entries. */
export function sentenceClips(message: string, clips: readonly VoiceClip[]): string[] {
  const phrases = clips.filter(c => c.id.startsWith('phrase_') &&
    /^(phrase_order_|phrase_double_|phrase_second_|phrase_hint_carry_)/.test(c.id));
  const addresses = clips.filter(c => c.id.startsWith('address_') && c.id !== 'address_then')
    .sort((a, b) => b.text.length - a.text.length);
  const then = clips.find(c => c.id === 'address_then');
  for (const phrase of phrases) {
    if (!message.startsWith(phrase.text)) continue;
    const rest = message.slice(phrase.text.length);
    const first = addresses.find(c => rest.startsWith(`${c.text}.`));
    if (!first) continue;
    const tail = rest.slice(first.text.length + 1);
    if (tail === '') return [phrase.text, first.text];
    if (!tail.startsWith(' Then ') || !then) continue;
    const second = addresses.find(c => tail === ` Then ${c.text}.`);
    if (second) return [phrase.text, first.text, then.text, second.text];
  }
  return [];
}

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
  private manifestLines: VoiceClip[] = [];
  private buffers = new Map<string, Promise<AudioBuffer>>();
  private shown: string | null = null;
  private pending: VoiceClip | null = null;
  private pendingQueue: string[] = [];
  private playing: Playing | null = null;
  private queue: string[] = [];
  private lineEnded: (() => void) | null = null;
  private request = 0;
  private unsubscribe: (() => void) | null = null;
  private disposed = false;
  readonly ready: Promise<void>;
  readonly played: string[] = [];
  readonly errors: string[] = [];

  constructor(private readonly output: VoiceOutput,
    private readonly fetcher: typeof fetch = (...args) => fetch(...args),
    private readonly base = `${import.meta.env.BASE_URL}assets/voice/`) {
    const done = loading.begin('Voices manifest');
    this.ready = this.fetcher(`${base}manifest.json`)
      .then(response => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); })
      .then((manifest: VoiceManifest) => {
        done(`${manifest.lines.length} voice clips indexed`);
        this.manifestLines = manifest.lines;
        for (const clip of manifest.lines) this.clips.set(clip.text, clip);
        if (this.shown !== null) this.show(this.shown, true);
        if (!this.playing) this.advance();
      })
      .catch(error => { done(`unavailable: ${String(error)}`); this.errors.push(`manifest: ${String(error)}`); });
    output.onRunning(() => {
      if (this.disposed) return;
      const clip = this.pending;
      this.pending = null;
      if (clip) void this.start(clip, this.pendingQueue);
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
    this.pendingQueue = [];
    const texts = message === null ? [] : this.clips.has(message) ? [message]
      : sentenceClips(message, this.manifestLines);
    const clip = this.clips.get(texts[0]);
    if (clip) { this.queue = []; this.lineEnded = null; void this.start(clip, texts.slice(1)); }
  }

  /** Speaks texts one after another (the start screen's story intro). A newly shown voiced HUD message
   *  cuts the narration; unvoiced messages do not. Waits for the manifest and for unlock. */
  narrate(texts: readonly string[]) {
    if (this.disposed) return;
    this.queue = [...texts];
    if (!this.playing) this.advance();
  }

  /** Plays one dialog line and reports its natural end; advancing manually cancels the callback. */
  playDialogLine(text: string, ended: () => void) {
    this.stopDialogLine();
    this.show(text, true);
    this.lineEnded = ended;
  }

  stopDialogLine() {
    this.lineEnded = null;
    this.pending = null;
    this.queue = [];
    ++this.request;
    if (this.playing) { this.playing.source.stop(); this.playing = null; this.output.duck(false); }
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

  private async start(clip: VoiceClip, nextQueue?: string[]) {
    const ctx = this.output.context(), bus = this.output.bus();
    if (!ctx || !bus || ctx.state !== 'running') {
      this.pending = clip;
      this.pendingQueue = nextQueue ?? this.queue;
      return;
    }
    const request = ++this.request; // only the newest voiced line may start
    let buffer: AudioBuffer;
    try { buffer = await this.load(clip, ctx); } catch (error) {
      this.errors.push(`${clip.id}: ${String(error)}`);
      if (request === this.request && !this.playing && !this.advance()) this.output.duck(false);
      return;
    }
    if (request !== this.request || this.disposed) return;
    if (nextQueue) this.queue = nextQueue;
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
      const ended = this.lineEnded;
      this.lineEnded = null;
      if (!this.advance()) this.output.duck(false);
      ended?.();
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
    this.pending = null;
    this.queue = [];
    this.unsubscribe?.();
    if (this.playing) { this.playing.source.stop(); this.playing = null; }
  }
}
