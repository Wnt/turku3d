import { TRACKS, type Stem, type Track } from './tracks';
import tunedParameters from './params.json';
export { STEMS, TRACKS, type Stem } from './tracks';
export type MusicState = 'explore' | 'mission' | 'menu';
export interface MusicParameters {
  volume: number; crossfadeBars: number; muffleCutoff: number; muffleAmount: number;
  muffleMs: number; hurryStart: number; tempoLift: number; stingerLeadBars: number;
  layerGains: Record<Stem, number>;
}
export const DEFAULT_PARAMETERS: MusicParameters = tunedParameters;
/** Free-play bars per track before moving on (about 2 to 3 minutes at the tracks' tempos). */
export const ROTATE_BARS = 64;
const clamp = (n: number, min = 0, max = 1) => Math.max(min, Math.min(max, Number.isFinite(n) ? n : min));
export function hurry(progress: number, start: number, lift: number) {
  const x = clamp((clamp(progress) - clamp(start, 0, 0.95)) / (1 - clamp(start, 0, 0.95)));
  return { intensity: x * x * (3 - 2 * x), tempoFactor: 1 + lift * x * x * (3 - 2 * x), percussion: x * x, cutoff: 2300 + 9500 * x, stinger: x >= 0.82 };
}
interface Change { atBeat: number; state: MusicState; track: number; fromState: MusicState; fromTrack: number; }
interface MusicSnapshot { bar: number; beat: number; tempo: number; state: MusicState; track: Track; layers: Stem[]; transitionBeat: number | null; }
/** Lookahead Web Audio sequencer. Caller starts it inside a user gesture. */
export class MusicSystem {
  readonly analyser: AnalyserNode;
  private master: GainNode;
  private filter: BiquadFilterNode;
  private context: AudioContext;
  private interval: ReturnType<typeof setInterval> | null = null;
  private beat = 0;
  private trackSince = 0;
  private beatTime = 0;
  private state: MusicState = 'explore';
  private track = 0;
  private pending: Change | null = null;
  private fading: Change | null = null;
  private deadline = 0;
  private activeMuffle = 0;
  private parameters: MusicParameters = structuredClone(DEFAULT_PARAMETERS);
  private started = false;
  private disposed = false;
  private lastTempo = TRACKS[0].bpm;
  private activeLayers: Stem[] = [];
  private voices = new Set<{ oscillator: OscillatorNode; gain: GainNode }>();
  constructor(context: AudioContext, destination: AudioNode = context.destination) {
    this.context = context;
    this.master = context.createGain(); this.filter = context.createBiquadFilter(); this.analyser = context.createAnalyser();
    this.filter.type = 'lowpass'; this.analyser.fftSize = 2048;
    this.master.connect(this.filter); this.filter.connect(this.analyser); this.analyser.connect(destination);
    this.setVolume(this.parameters.volume);
  }
  start() {
    if (this.disposed) return;
    // Resume is initiated synchronously so the browser's activation is retained.
    void this.context.resume();
    if (!this.started) { this.started = true; this.beatTime = this.context.currentTime + 0.04; }
    if (!this.interval) this.interval = setInterval(() => this.tick(), 25);
    this.tick();
  }
  stop() { if (this.interval) clearInterval(this.interval); this.interval = null; }
  dispose() { this.stop(); this.disposed = true;
    for (const voice of this.voices) { voice.oscillator.disconnect(); voice.gain.disconnect(); }
    this.voices.clear(); this.master.disconnect(); this.filter.disconnect(); this.analyser.disconnect(); }
  private remember(oscillator: OscillatorNode, gain: GainNode) {
    const voice = { oscillator, gain }; this.voices.add(voice);
    oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); this.voices.delete(voice); };
  }
  setState(state: MusicState) { this.request(state, this.track); }
  nextTrack() { this.request(this.state, (this.track + 1) % TRACKS.length); }
  selectTrack(index: number) { if (Number.isInteger(index) && index >= 0 && index < TRACKS.length) this.request(this.state, index); }
  private request(state: MusicState, track: number) {
    if (state === this.state && track === this.track && !this.pending) return;
    const atBeat = Math.ceil(this.beat / 4) * 4;
    this.pending = { atBeat, state, track, fromState: this.state, fromTrack: this.track };
  }
  setDeadline(progress: number) { this.deadline = clamp(progress); }
  /** A finished delivery resolves the cadence and moves free play on to the next track. */
  deliver() {
    this.chime(this.context.currentTime + 0.02, TRACKS[this.track], true); this.setDeadline(0);
    this.request('explore', (this.track + 1) % TRACKS.length);
  }
  setMuffled(amount: number) { this.activeMuffle = clamp(amount); this.applyMuffle(); }
  setVolume(value: number) { this.parameters.volume = clamp(value); this.applyMuffle(); }
  setParameters(patch: Partial<MusicParameters>) {
    this.parameters = { ...this.parameters, ...patch, layerGains: { ...this.parameters.layerGains, ...patch.layerGains } };
    this.setVolume(this.parameters.volume);
  }
  getParameters(): MusicParameters { return structuredClone(this.parameters); }
  snapshot(): MusicSnapshot {
    return { bar: Math.floor(this.beat / 4) + 1, beat: this.beat % 4 + 1, tempo: this.lastTempo,
      state: this.state, track: TRACKS[this.track], layers: this.activeLayers, transitionBeat: this.pending?.atBeat ?? null };
  }
  private applyMuffle() {
    const p = this.parameters, amount = this.activeMuffle, t = this.context.currentTime, tau = Math.max(0.001, p.muffleMs / 3000);
    this.filter.frequency.setTargetAtTime(18000 * (1 - amount) + p.muffleCutoff * amount, t, tau);
    this.master.gain.setTargetAtTime(p.volume * (1 - amount * 0.18), t, tau);
  }
  /** Public for deterministic fake-clock tests; the interval calls the same method. */
  tick() {
    if (!this.started || this.disposed || this.context.state !== 'running') return;
    const now = this.context.currentTime;
    if (this.beatTime < now) this.beatTime = now + 0.01; // suspended tabs never schedule into the past
    while (this.beatTime < now + 0.12) {
      const old = this.pending;
      if (old && this.beat >= old.atBeat) {
        if (old.track !== old.fromTrack) this.trackSince = old.atBeat;
        this.state = old.state; this.track = old.track; this.pending = null; this.fading = old;
        if (old.state === 'mission' && old.fromState !== 'mission') this.chime(this.beatTime, TRACKS[this.track], false);
      }
      // Free play moves on to the next track every ROTATE_BARS; a mission keeps its track until hand-over.
      if (this.state === 'explore' && !this.pending && this.beat - this.trackSince >= ROTATE_BARS * 4) this.nextTrack();
      const track = TRACKS[this.track];
      this.activeLayers = [];
      const curve = hurry(this.deadline, (track.liftStart ?? DEFAULT_PARAMETERS.hurryStart) + this.parameters.hurryStart - DEFAULT_PARAMETERS.hurryStart, (track.tempoLift ?? DEFAULT_PARAMETERS.tempoLift) * this.parameters.tempoLift / DEFAULT_PARAMETERS.tempoLift);
      this.lastTempo = track.bpm * (this.state === 'mission' ? curve.tempoFactor : 1);
      const fadeBeats = Math.max(1, this.parameters.crossfadeBars * 4);
      const fade = this.fading;
      const weight = fade ? clamp((this.beat - fade.atBeat + 1) / fadeBeats) : 1;
      if (fade && weight < 1) this.scheduleBeat(TRACKS[fade.fromTrack], fade.fromState, this.beat, this.beatTime, 1 - weight, curve);
      if (weight === 1) this.fading = null;
      this.scheduleBeat(track, this.state, this.beat, this.beatTime, weight, curve);
      this.beat++; this.beatTime += 60 / this.lastTempo;
    }
  }
  private scheduleBeat(track: Track, state: MusicState, beat: number, time: number, weight: number, curve: ReturnType<typeof hurry>) {
    if (state === 'menu' || weight <= 0) { this.activeLayers = []; return; }
    const p = this.parameters, mission = state === 'mission', bar = Math.floor(beat / 4);
    const chord = track.progression[bar % track.progression.length];
    const hz = track.root * 2 ** (chord / 12);
    const layer = (stem: Stem, gain: number, freq: number, length: number, wave = track.wave) => {
      const level = gain * p.layerGains[stem] * weight;
      if (level < 0.001) return;
      this.activeLayers.push(stem);
      const osc = this.context.createOscillator(), env = this.context.createGain();
      osc.type = wave; osc.frequency.setValueAtTime(freq, time);
      env.gain.setValueAtTime(0, time); env.gain.linearRampToValueAtTime(level, time + 0.012);
      env.gain.setTargetAtTime(0, time + Math.min(length, 0.22), Math.max(0.03, length / 4));
      osc.connect(env); env.connect(this.master); osc.start(time); osc.stop(time + length + 0.15);
      this.remember(osc, env);
    };
    if (beat % 4 === 0) layer('pad', 0.085, hz, 1.45, 'sine');
    if (beat % 2 === 0) layer('bass', mission ? 0.12 : 0.075, hz / 2, 0.32, 'triangle');
    if (mission || beat % 2 === 0) layer('drums', mission ? 0.12 : 0.06, beat % 2 ? 160 : 65, 0.095, 'sine');
    if (beat % 4 === 2 || (mission && beat % 2 === 1)) layer('lead', mission ? 0.09 : 0.045, hz * (beat % 2 ? 1.5 : 2), 0.18);
    if (mission) {
      layer('percussion', 0.025 + curve.percussion * 0.08, 2600, 0.035, 'square');
      if (curve.intensity > 0.35) layer(track.panicStem ?? 'panic', curve.intensity * 0.09, hz * 3, 0.11, 'sawtooth');
      if (this.deadline >= 1 - p.stingerLeadBars * 0.09 && beat % 4 === 0) this.chime(time, track, false);
    }
    const cutoff = mission ? curve.cutoff : 6500;
    this.filter.frequency.setTargetAtTime(Math.min(cutoff, 18000 * (1 - this.activeMuffle) + p.muffleCutoff * this.activeMuffle), time, Math.max(0.06, p.muffleMs / 3000));
  }
  private chime(time: number, track: Track, resolved: boolean) {
    const root = track.root * (resolved ? 2 : 3);
    for (const [i, ratio] of (resolved ? [1, 1.25, 1.5] : [1, 1.2]).entries()) {
      const t = Math.max(this.context.currentTime, time + i * 0.09), osc = this.context.createOscillator(), gain = this.context.createGain();
      osc.type = 'sine'; osc.frequency.setValueAtTime(root * ratio, t);
      gain.gain.setValueAtTime(0, t); gain.gain.linearRampToValueAtTime(0.09, t + 0.01); gain.gain.setTargetAtTime(0, t + 0.07, 0.1);
      osc.connect(gain); gain.connect(this.master); osc.start(t); osc.stop(t + 0.45);
      this.remember(osc, gain);
    }
  }
}
