import type { V3 } from '../core/origin';
import { SOUNDS, ambience, carMix, clamp, oneShot, type AudioEvent, type Channel, type Sound } from './mix';

type Voice = { source: AudioBufferSourceNode; gain: GainNode; panner?: PannerNode };
const CHANNELS: Channel[] = ['master', 'footsteps', 'vehicle', 'ambience', 'ui'];

/** Owns all Web Audio nodes; gameplay only emits semantic events. No renderer dependency. */
export class GameAudio {
  private context: AudioContext | null = null;
  private channels = new Map<Channel, GainNode>();
  private buffers = new Map<Sound, AudioBuffer>();
  private loops = new Map<Sound, Voice>();
  private voices = new Set<Voice>();
  private loading: Promise<void> | null = null;
  private disposed = false;
  private paused = false;
  private pendingConnect = false;
  private listener: Extract<AudioEvent, { type: 'listener' }> | null = null;
  private car: Extract<AudioEvent, { type: 'car' }> | null = null;
  private panel?: HTMLElement;
  private muteButton?: HTMLButtonElement;
  private summary?: HTMLElement;
  readonly volumes: Record<Channel, number> = { master: 0.65, footsteps: 0.8, vehicle: 0.8, ambience: 0.7, ui: 0.6 };
  muted = false;
  readonly errors: string[] = [];
  readonly played: Partial<Record<Sound, number>> = {};

  constructor(private readonly factory = () => new AudioContext(),
    private readonly fetcher: typeof fetch = (...args) => fetch(...args),
    private readonly base = `${import.meta.env.BASE_URL}assets/audio/`) {}

  /** Called synchronously inside the first input handler so Chrome receives user activation. */
  unlock(): Promise<void> {
    if (this.disposed) return Promise.resolve();
    try {
      if (!this.context) {
        const ctx = this.context = this.factory();
        const master = ctx.createGain();
        const limiter = ctx.createDynamicsCompressor();
        limiter.threshold.value = -6; limiter.ratio.value = 12;
        master.connect(limiter); limiter.connect(ctx.destination);
        this.channels.set('master', master);
        for (const name of CHANNELS.slice(1)) {
          const bus = ctx.createGain(); bus.connect(master); this.channels.set(name, bus);
        }
        this.applyVolumes();
      }
      const ctx = this.context;
      // Do not await asset downloads before resume: the activation would already have expired.
      const resume = ctx.state === 'running' ? Promise.resolve() : ctx.resume();
      this.loading ??= Promise.all(SOUNDS.map(async sound => {
        try {
          const response = await this.fetcher(`${this.base}${sound}.ogg`);
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const buffer = await ctx.decodeAudioData(await response.arrayBuffer());
          if (!this.disposed) this.buffers.set(sound, buffer);
        } catch (error) { this.errors.push(`${sound}: ${String(error)}`); }
      })).then(() => {
        if (this.disposed) return;
        this.refreshLoops();
      });
      return Promise.all([resume, this.loading]).then(() => {
        if (this.disposed || ctx.state !== 'running') return;
        if (this.summary) this.summary.textContent = 'Audio';
        if (this.pendingConnect) { this.pendingConnect = false; this.emit({ type: 'gamepad-connected' }); }
      }).catch(error => { this.errors.push(String(error)); });
    } catch (error) { this.errors.push(String(error)); return Promise.resolve(); }
  }

  emit(event: AudioEvent) {
    if (this.disposed) return;
    if (event.type === 'mute') {
      this.muted = !this.muted; this.applyVolumes();
      if (this.muteButton) this.muteButton.textContent = this.muted ? 'Unmute (M / Menu)' : 'Mute (M / Menu)';
      return;
    }
    if (event.type === 'volume') { this.volumes[event.channel] = clamp(event.value); this.applyVolumes(); return; }
    if (event.type === 'listener') {
      this.listener = event; this.paused = event.paused;
      if (this.context) {
        const l = this.context.listener;
        this.vector([l.positionX, l.positionY, l.positionZ], event.position);
        this.vector([l.forwardX, l.forwardY, l.forwardZ], event.forward);
        this.vector([l.upX, l.upY, l.upZ], event.up);
      }
      this.refreshLoops(); return;
    }
    if (event.type === 'car') { this.car = event; this.refreshLoops(); return; }
    if (event.type === 'gamepad-connected' && (!this.buffers.has('connect') || this.context?.state !== 'running')) { this.pendingConnect = true; return; }
    if (this.paused && event.type !== 'gamepad-connected') return;
    const shot = oneShot(event);
    if (shot && shot.gain > 0) this.start(shot.sound, shot.channel, shot.gain, false, shot.position);
  }

  private applyVolumes() {
    for (const [name, node] of this.channels) this.param(node.gain, name === 'master' && this.muted ? 0 : this.volumes[name]);
  }
  private param(param: AudioParam, value: number) { param.setTargetAtTime(value, this.context!.currentTime, 0.04); }
  private vector(params: AudioParam[], v: V3) { [v.x, v.y, v.z].forEach((n, i) => this.param(params[i], n)); }

  private start(sound: Sound, channel: Channel, gain: number, loop: boolean, position?: V3): Voice | null {
    const ctx = this.context, buffer = this.buffers.get(sound);
    if (!ctx || !buffer || this.disposed || (!loop && (this.voices.size >= 24 || ctx.state !== 'running'))) return null;
    const source = ctx.createBufferSource(), volume = ctx.createGain();
    source.buffer = buffer; source.loop = loop; volume.gain.value = gain;
    source.connect(volume);
    let panner: PannerNode | undefined;
    if (position) {
      panner = ctx.createPanner(); panner.panningModel = 'HRTF'; panner.distanceModel = 'inverse';
      panner.refDistance = channel === 'ambience' ? 25 : 4; panner.maxDistance = 300; panner.rolloffFactor = 0.7;
      this.vector([panner.positionX, panner.positionY, panner.positionZ], position);
      volume.connect(panner); panner.connect(this.channels.get(channel)!);
    } else volume.connect(this.channels.get(channel)!);
    const voice = { source, gain: volume, panner };
    source.onended = () => { source.disconnect(); volume.disconnect(); panner?.disconnect(); this.voices.delete(voice); };
    this.voices.add(voice); source.start();
    this.played[sound] = (this.played[sound] ?? 0) + 1;
    return voice;
  }

  private loop(sound: Sound, channel: Channel, gain: number, position?: V3, rate = 1) {
    let voice = this.loops.get(sound);
    if (!voice) {
      voice = this.start(sound, channel, 0, true, position) ?? undefined;
      if (voice) this.loops.set(sound, voice);
    }
    if (!voice) return;
    this.param(voice.gain.gain, this.paused ? 0 : gain);
    this.param(voice.source.playbackRate, rate);
    if (position && voice.panner) this.vector([voice.panner.positionX, voice.panner.positionY, voice.panner.positionZ], position);
  }

  private refreshLoops() {
    if (this.listener) {
      const mix = ambience(this.listener.position);
      this.loop('city', 'ambience', mix.city);
      this.loop('wind', 'ambience', mix.wind);
      this.loop('traffic', 'ambience', mix.traffic, { x: 100, y: 2, z: 180 });
      this.loop('river', 'ambience', mix.water, mix.river);
      this.loop('gulls', 'ambience', mix.gulls, { ...mix.river, y: 16 });
    }
    if (this.car) {
      const c = this.car, mix = carMix(c.rpm, c.throttle, c.speed, c.slip, c.running);
      this.loop('engine', 'vehicle', mix.engine, c.position, mix.rate);
      this.loop('roll', 'vehicle', mix.roll, c.position);
      this.loop('screech', 'vehicle', mix.screech, c.position);
    }
  }

  mount(parent: HTMLElement) {
    const panel = this.panel = document.createElement('details');
    panel.style.cssText = 'position:fixed;right:12px;bottom:44px;color:white;background:#18212b;padding:8px;z-index:5;font:12px sans-serif';
    const summary = this.summary = document.createElement('summary');
    summary.textContent = 'Audio · click to enable'; summary.onclick = () => { void this.unlock(); }; panel.append(summary);
    this.muteButton = document.createElement('button'); this.muteButton.textContent = 'Mute (M / Menu)';
    this.muteButton.onclick = () => this.emit({ type: 'mute' }); panel.append(this.muteButton);
    for (const channel of CHANNELS) {
      const label = document.createElement('label'); label.style.display = 'block'; label.textContent = channel;
      const slider = document.createElement('input'); slider.type = 'range'; slider.min = '0'; slider.max = '1'; slider.step = '0.01';
      slider.value = String(this.volumes[channel]); slider.setAttribute('aria-label', `${channel} volume`);
      slider.oninput = () => this.emit({ type: 'volume', channel, value: Number(slider.value) });
      label.append(slider); panel.append(label);
    }
    parent.append(panel);
  }

  snapshot() { return { state: this.context?.state ?? 'locked', loaded: this.buffers.size, loops: this.loops.size,
    voices: this.voices.size, muted: this.muted, volumes: { ...this.volumes }, played: { ...this.played }, errors: [...this.errors] }; }

  dispose() {
    this.disposed = true;
    for (const voice of this.voices) { voice.source.stop(); voice.source.disconnect(); voice.gain.disconnect(); voice.panner?.disconnect(); }
    this.voices.clear(); this.loops.clear(); this.buffers.clear(); this.channels.clear(); this.panel?.remove();
    void this.context?.close().catch(() => {});
  }
}
