import type { V3 } from '../../core/origin';
import type { VehicleKind } from '../../physics/constants';
import { MIX_HZ, POSITION_EPSILON, frequencyEpsilon, glide } from '../automation';
import { TYRE_PROCESSOR, type TyreProcessorOptions } from './messages';
import { TYRE_KINDS, type TyreFrame } from './tyre';
import { distanceCutoff } from './voices';

/** Tyre voices of cars and scooters for GameAudio: the local vehicle plus the nearest audible ones of other players,
 *  each an AudioWorkletNode running TyreSynth, then (remote) a distance low-pass, and a panner. A truck's tyres are part
 *  of its engine voice (voices.ts). */

export interface TyreVoiceState { kind: VehicleKind; frame: TyreFrame; position: V3; local: boolean }

/** At most this many tyre voices: the local vehicle plus the 3 nearest audible within TYRE_RANGE. */
export const TYRE_VOICES = 4;
export const TYRE_RANGE = 70;
/** A voice is released 10 m beyond the range, or once its vehicle has been inaudible for LINGER seconds. */
const RELEASE_RANGE = 80, LINGER = 1.5;
/** A candidate not updated for this long is dropped. */
const STALE_SECONDS = 0.5;

/** A vehicle is worth a remote voice while it slides, or rolls fast enough to be heard passing. */
export const tyreAudible = (f: TyreFrame) => f.slide > 0.4 || f.speed > 4;

export interface TyreCandidate { id: string; local: boolean; distance: number; active: boolean; audible: boolean }
/** Picks the voiced vehicles: the local one always, then the nearest audible within range (an active voice is kept
 *  through LINGER and to the release range, so a car at the edge does not flap). */
export function selectTyreVoices(candidates: readonly TyreCandidate[]): Set<string> {
  const chosen = new Set<string>();
  for (const c of candidates) if (c.local) chosen.add(c.id);
  const remote = candidates.filter(c => !c.local && c.audible && c.distance <= (c.active ? RELEASE_RANGE : TYRE_RANGE))
    .sort((a, b) => a.distance - b.distance);
  for (const c of remote) { if (chosen.size >= TYRE_VOICES) break; chosen.add(c.id); }
  return chosen;
}

const hash = (id: string) => { let h = 2166136261; for (let i = 0; i < id.length; i++) h = Math.imul(h ^ id.charCodeAt(i), 16777619); return h >>> 0; };
const silent = (f: TyreFrame) => f.slide < 0.02 && f.speed < 0.3;

interface Candidate { id: string; state: TyreVoiceState; seen: number; heard: number }
interface Voice { id: string; kind: VehicleKind; local: boolean; node: AudioWorkletNode; gain: GainNode; filter?: BiquadFilterNode; panner: PannerNode;
  /** The last frame posted, and whether it was silent: a repeated frame or further silence is not posted again. */
  last: TyreFrame | null; quiet: boolean;
  /** Context time of the next panner and cutoff update (at most MIX_HZ; the worklet still gets every frame). */
  moveAt: number }

export interface TyreVoiceHost {
  context(): AudioContext | null;
  bus(): AudioNode | null;
  /** Other voices in use (the shared 24-voice budget). */
  used(): number;
  error(message: string): void;
}

export class TyreVoices {
  private readonly candidates = new Map<string, Candidate>();
  private readonly voices = new Map<string, Voice>();
  private module: Promise<void> | null = null;
  private ready = false;
  private listener: V3 | null = null;
  private paused = false;

  constructor(private readonly host: TyreVoiceHost) {}

  /** Records one frame of a vehicle's tyres (null releases it). Call once per frame per vehicle. */
  update(id: string, state: TyreVoiceState | null) {
    if (!state) { this.candidates.delete(id); this.release(id); return; }
    const now = this.now();
    let c = this.candidates.get(id);
    if (!c) { c = { id, state, seen: now, heard: -Infinity }; this.candidates.set(id, c); }
    c.state = state; c.seen = now;
    if (tyreAudible(state.frame)) c.heard = now;
    const voice = this.voices.get(id);
    if (voice ? voice.kind !== state.kind : state.local || tyreAudible(state.frame)) this.refresh();
    this.post(id);
  }

  /** Listener moved (once per frame): re-pick the voices and drop stale vehicles. */
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
    const chosen = selectTyreVoices([...this.candidates.values()].map(c => ({ id: c.id, local: c.state.local,
      distance: at ? Math.hypot(c.state.position.x - at.x, c.state.position.y - at.y, c.state.position.z - at.z) : 0,
      active: this.voices.has(c.id), audible: now - c.heard <= LINGER })));
    for (const [id, v] of this.voices) if (!chosen.has(id) || v.kind !== this.candidates.get(id)!.state.kind) this.release(id);
    const ctx = this.host.context();
    if (!ctx || !chosen.size || !this.load(ctx)) return;
    for (const id of chosen) {
      if (this.voices.has(id)) continue;
      if (this.host.used() + this.voices.size >= 24) break;
      this.create(ctx, id);
    }
  }

  private load(ctx: AudioContext) {
    if (this.ready) return true;
    if (!this.module && ctx.audioWorklet) {
      this.module = import('./tyre.worklet.ts?worker&url')
        .then(m => ctx.audioWorklet.addModule(m.default))
        .then(() => { this.ready = true; this.refresh(); })
        .catch(e => this.host.error(`tyre worklet: ${String(e)}`));
    }
    return false;
  }

  private create(ctx: AudioContext, id: string) {
    const c = this.candidates.get(id)!, bus = this.host.bus();
    if (!bus) return;
    const tuning = TYRE_KINDS[c.state.kind];
    const options: TyreProcessorOptions = { seed: hash(id), pitch: tuning.pitch, roll: tuning.roll };
    try {
      const node = new AudioWorkletNode(ctx, TYRE_PROCESSOR, { numberOfInputs: 0, numberOfOutputs: 1, outputChannelCount: [1], processorOptions: options });
      const gain = ctx.createGain(); gain.gain.value = 0;
      const panner = ctx.createPanner(); panner.distanceModel = 'inverse';
      node.connect(gain);
      const voice: Voice = { id, kind: c.state.kind, local: c.state.local, node, gain, panner, last: null, quiet: true, moveAt: -Infinity };
      if (c.state.local) {
        // The driven vehicle, heard from the chase camera: the placement the sample loops it replaces had.
        panner.panningModel = 'HRTF'; panner.refDistance = 4; panner.maxDistance = 300; panner.rolloffFactor = 0.7;
        gain.connect(panner);
      } else {
        panner.panningModel = 'equalpower'; panner.refDistance = 6; panner.maxDistance = 150; panner.rolloffFactor = 1;
        const filter = voice.filter = ctx.createBiquadFilter(); filter.type = 'lowpass'; filter.Q.value = 0.5;
        gain.connect(filter); filter.connect(panner);
      }
      panner.connect(bus);
      this.voices.set(id, voice);
      this.level(voice);
    } catch (e) { this.host.error(`tyre voice: ${String(e)}`); }
  }

  private level(v: Voice) {
    const ctx = this.host.context();
    if (ctx) glide(v.gain.gain, this.paused ? 0 : TYRE_KINDS[v.kind].gain, ctx.currentTime, 0.05);
  }

  private post(id: string) {
    const v = this.voices.get(id), c = this.candidates.get(id), ctx = this.host.context();
    if (!v || !c || !ctx) return;
    const frame = c.state.frame, quiet = silent(frame);
    if (frame !== v.last && (!quiet || !v.quiet)) v.node.port.postMessage(frame);
    v.last = frame; v.quiet = quiet;
    const t = ctx.currentTime;
    if (ctx.state !== 'running' || t < v.moveAt) return;
    v.moveAt = t + 1 / MIX_HZ;
    const p = c.state.position, l = this.listener;
    if (v.panner.positionX) {
      glide(v.panner.positionX, p.x, t, 0.03, POSITION_EPSILON); glide(v.panner.positionY, p.y, t, 0.03, POSITION_EPSILON);
      glide(v.panner.positionZ, p.z, t, 0.03, POSITION_EPSILON);
    } else v.panner.setPosition(p.x, p.y, p.z);
    if (v.filter) {
      const cutoff = distanceCutoff(l ? Math.hypot(p.x - l.x, p.y - l.y, p.z - l.z) : 10);
      glide(v.filter.frequency, cutoff, t, 0.05, frequencyEpsilon(cutoff));
    }
  }

  /** Fades the voice out and frees its nodes. */
  private release(id: string) {
    const v = this.voices.get(id), ctx = this.host.context();
    if (!v) return;
    this.voices.delete(id);
    if (!ctx) { this.disconnect(v); return; }
    v.gain.gain.cancelScheduledValues(ctx.currentTime);
    v.gain.gain.setTargetAtTime(0, ctx.currentTime, 0.05);
    setTimeout(() => this.disconnect(v), 400);
  }

  private disconnect(v: Voice) {
    try { v.node.port.postMessage('stop'); } catch { /* closed */ }
    v.node.disconnect(); v.gain.disconnect(); v.filter?.disconnect(); v.panner.disconnect();
  }

  get size() { return this.voices.size; }

  snapshot() {
    return { ready: this.ready, candidates: this.candidates.size,
      voices: [...this.voices.values()].map(v => { const f = this.candidates.get(v.id)?.state.frame;
        return { id: v.id, kind: v.kind, local: v.local, slide: +(f?.slide ?? 0).toFixed(2), load: +(f?.load ?? 1).toFixed(2), speed: +(f?.speed ?? 0).toFixed(1),
          squeal: f?.squeal ?? 0 }; }) };
  }

  dispose() {
    for (const v of this.voices.values()) this.disconnect(v);
    this.voices.clear(); this.candidates.clear();
  }
}
