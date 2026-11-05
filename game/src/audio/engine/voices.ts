import type { V3 } from '../../core/origin';
import type { WheelState } from '../../physics/protocol';
import { ChassisTracker, type ChassisFrame } from './chassis';
import type { EngineDrive } from './drivetrain';
import { V8_PROCESSOR, type V8Message, type V8ProcessorOptions } from './messages';

/** The truck engine voice pool for GameAudio (SPEC §4.6): the local truck plus the nearest audible remote ones, each
 *  an AudioWorkletNode running v8core + chassis, then a distance low-pass and an equal-power panner. */

export interface EngineVoiceState { drive: EngineDrive; speed: number; position: V3; velocity?: V3; local: boolean;
  wheels: readonly WheelState[]; rearSlew: number }

/** At most this many truck engine voices: the local one plus the 3 nearest within VOICE_RANGE. */
export const ENGINE_VOICES = 4;
export const VOICE_RANGE = 120;
/** A voice is released 10 m beyond the range, so a truck at the edge does not flap. */
const RELEASE_RANGE = 130;
/** Remote voices nearer than this rank keep all 32 partials; farther ones use 16. */
const FULL_REMOTE = 2;
/** A candidate not updated for this long is dropped. */
const STALE_SECONDS = 0.5;
export const VOICE_GAIN = 0.9;
const COCKPIT_GAIN = 0.75;

/** Distance low-pass: 8 kHz at 10 m falling (log) to 2 kHz at 100 m. */
export function distanceCutoff(distance: number) {
  const d = Math.max(10, Math.min(100, Number.isFinite(distance) ? distance : 100));
  return 8000 * (2000 / 8000) ** ((d - 10) / 90);
}

export interface VoiceCandidate { id: string; local: boolean; distance: number; active: boolean }
/** Picks the voiced trucks: the local one always, then the nearest within range (an active voice is kept until it
 *  passes the release range); returns id → partial count. */
export function selectVoices(candidates: readonly VoiceCandidate[]): Map<string, 16 | 32> {
  const chosen = new Map<string, 16 | 32>();
  for (const c of candidates) if (c.local) chosen.set(c.id, 32);
  const remote = candidates.filter(c => !c.local && c.distance <= (c.active ? RELEASE_RANGE : VOICE_RANGE))
    .sort((a, b) => a.distance - b.distance);
  let rank = 0;
  for (const c of remote) {
    if (chosen.size >= ENGINE_VOICES) break;
    chosen.set(c.id, rank++ < FULL_REMOTE ? 32 : 16);
  }
  return chosen;
}

const hash = (id: string) => { let h = 2166136261; for (let i = 0; i < id.length; i++) h = Math.imul(h ^ id.charCodeAt(i), 16777619); return h >>> 0; };

interface Candidate { id: string; state: EngineVoiceState; seen: number; tracker: ChassisTracker; frame: ChassisFrame | null; at: number }
interface Voice { id: string; node: AudioWorkletNode; gain: GainNode; filter?: BiquadFilterNode; panner?: PannerNode;
  partials: 16 | 32; local: boolean; releasing: number }

export interface EngineVoiceHost {
  context(): AudioContext | null;
  bus(): AudioNode | null;
  /** Other voices in use (the shared 24-voice budget). */
  used(): number;
  error(message: string): void;
}

export class EngineVoices {
  private readonly candidates = new Map<string, Candidate>();
  private readonly voices = new Map<string, Voice>();
  private module: Promise<void> | null = null;
  private ready = false;
  private listener: V3 | null = null;
  private paused = false;
  private chosen = new Map<string, 16 | 32>();

  constructor(private readonly host: EngineVoiceHost) {}

  /** Records one frame of a truck's state (null releases it). Call once per frame per audible truck. */
  update(id: string, state: EngineVoiceState | null) {
    if (!state) { this.candidates.delete(id); this.release(id); return; }
    const now = this.now();
    let c = this.candidates.get(id);
    if (!c) { c = { id, state, seen: now, tracker: new ChassisTracker(), frame: null, at: now }; this.candidates.set(id, c); }
    const dt = Math.min(0.25, Math.max(1 / 240, now - c.at || 1 / 60));
    c.state = state; c.seen = now; c.at = now;
    c.frame = c.tracker.step({ dt, wheels: state.wheels, speed: state.speed, rearSlew: state.rearSlew, heave: state.position.y });
    if (!this.voices.has(id)) this.refresh();
    this.post(id);
  }

  /** Listener moved (once per frame): re-pick the voices, drop stale trucks, follow positions. */
  listen(position: V3, paused: boolean) {
    this.listener = position;
    if (paused !== this.paused) { this.paused = paused; for (const v of this.voices.values()) this.level(v); }
    this.refresh();
  }

  private now() { return this.host.context()?.currentTime || performance.now() / 1000; }

  private refresh() {
    const now = this.now();
    for (const [id, c] of this.candidates) if (now - c.seen > STALE_SECONDS) { this.candidates.delete(id); this.release(id); }
    const at = this.listener;
    this.chosen = selectVoices([...this.candidates.values()].map(c => ({ id: c.id, local: c.state.local,
      distance: at ? Math.hypot(c.state.position.x - at.x, c.state.position.y - at.y, c.state.position.z - at.z) : 0,
      active: this.voices.has(c.id) })));
    for (const id of this.voices.keys()) if (!this.chosen.has(id)) this.release(id);
    const ctx = this.host.context();
    if (!ctx || !this.load(ctx)) return;
    for (const [id, partials] of this.chosen) {
      const v = this.voices.get(id);
      if (v) { if (v.partials !== partials) v.partials = partials; continue; }
      if (this.host.used() + this.voices.size >= 24) break;
      this.create(ctx, id, partials);
    }
  }

  private load(ctx: AudioContext) {
    if (this.ready) return true;
    if (!this.module && ctx.audioWorklet) {
      this.module = import('./v8.worklet.ts?worker&url')
        .then(m => ctx.audioWorklet.addModule(m.default))
        .then(() => { this.ready = true; this.refresh(); })
        .catch(e => this.host.error(`engine worklet: ${String(e)}`));
    }
    return false;
  }

  private create(ctx: AudioContext, id: string, partials: 16 | 32) {
    const c = this.candidates.get(id)!, bus = this.host.bus();
    if (!bus) return;
    const cold = c.state.local && Math.abs(c.state.speed) < 0.5 && c.state.drive.rpm < 1100;
    const options: V8ProcessorOptions = { seed: hash(id), partials, running: !cold };
    try {
      const node = new AudioWorkletNode(ctx, V8_PROCESSOR, { numberOfInputs: 0, numberOfOutputs: 1, outputChannelCount: [1], processorOptions: options });
      const gain = ctx.createGain(); gain.gain.value = 0;
      node.connect(gain);
      const voice: Voice = { id, node, gain, partials, local: c.state.local, releasing: 0 };
      if (c.state.local) gain.connect(bus);
      else {
        const filter = voice.filter = ctx.createBiquadFilter(); filter.type = 'lowpass'; filter.Q.value = 0.5;
        const panner = voice.panner = ctx.createPanner(); panner.panningModel = 'equalpower'; panner.distanceModel = 'inverse';
        panner.refDistance = 6; panner.maxDistance = 150; panner.rolloffFactor = 1;
        gain.connect(filter); filter.connect(panner); panner.connect(bus);
      }
      this.voices.set(id, voice);
      this.level(voice);
    } catch (e) { this.host.error(`engine voice: ${String(e)}`); }
  }

  private level(v: Voice) {
    const ctx = this.host.context();
    if (!ctx) return;
    v.gain.gain.setTargetAtTime(this.paused ? 0 : (v.local ? COCKPIT_GAIN : 1) * VOICE_GAIN, ctx.currentTime, 0.05);
  }

  private post(id: string) {
    const v = this.voices.get(id), c = this.candidates.get(id), ctx = this.host.context();
    if (!v || !c || !ctx || v.releasing) return;
    const d = c.state.drive;
    const message: V8Message = { p: { rpm: d.rpm, load: d.load, throttle: d.throttle, limiter: d.limiter, shifting: d.shifting,
      running: true, cockpit: v.local, partials: v.partials }, c: c.frame ?? undefined };
    v.node.port.postMessage(message);
    if (v.panner && v.filter) {
      const p = c.state.position, t = ctx.currentTime;
      if (v.panner.positionX) { v.panner.positionX.setTargetAtTime(p.x, t, 0.03); v.panner.positionY.setTargetAtTime(p.y, t, 0.03); v.panner.positionZ.setTargetAtTime(p.z, t, 0.03); }
      else v.panner.setPosition(p.x, p.y, p.z);
      const l = this.listener, distance = l ? Math.hypot(p.x - l.x, p.y - l.y, p.z - l.z) : 10;
      v.filter.frequency.setTargetAtTime(distanceCutoff(distance), t, 0.05);
    }
  }

  /** The engine shuts down (rpm decays) and the nodes are freed after a second. */
  private release(id: string) {
    const v = this.voices.get(id), ctx = this.host.context();
    if (!v) return;
    this.voices.delete(id);
    if (!ctx) { this.disconnect(v); return; }
    const last = this.candidates.get(id)?.state.drive;
    v.releasing = 1;
    v.node.port.postMessage({ p: { rpm: last?.rpm ?? 930, load: -0.3, throttle: 0, limiter: false, shifting: false, running: false,
      cockpit: v.local, partials: v.partials } } satisfies V8Message);
    v.gain.gain.setTargetAtTime(0, ctx.currentTime + (v.local ? 0.5 : 0), 0.15);
    setTimeout(() => this.disconnect(v), 1200);
  }

  private disconnect(v: Voice) {
    try { v.node.port.postMessage('stop'); } catch { /* closed */ }
    v.node.disconnect(); v.gain.disconnect(); v.filter?.disconnect(); v.panner?.disconnect();
  }

  get size() { return this.voices.size; }

  snapshot() {
    return { ready: this.ready, candidates: this.candidates.size, chosen: Object.fromEntries(this.chosen),
      voices: [...this.voices.values()].map(v => ({ id: v.id, local: v.local, partials: v.partials,
        rpm: Math.round(this.candidates.get(v.id)?.state.drive.rpm ?? 0) })) };
  }

  dispose() {
    for (const v of this.voices.values()) this.disconnect(v);
    this.voices.clear(); this.candidates.clear();
  }
}
