import { CITY } from '../core/city';
import type { V3 } from '../core/origin';
import { glide } from './automation';

/** Gull calls: now and then, only by the river bank and in the harbour, each a one-shot heard from where the bird is.
 *
 *  `GullScheduler` decides when and where (pure, seeded in tests); `GullVoices` plays the call: the listener's
 *  distance sets its level (PannerNode), an air-absorption low-pass, a reflection off the water (a short comb) and
 *  how much of it arrives as echo from the quays (a generated impulse response). The two calls are harmonic-plus-noise
 *  resyntheses of measured long calls (public/assets/audio/LICENSES.md), fetched when the player first nears the water. */

export const GULL_SOUNDS = ['gull-herring-longcall', 'gull-common-longcall'] as const;
export type GullSound = typeof GULL_SOUNDS[number];
/** Length of each call (s): the next one is scheduled from its end. */
const SECONDS: Record<GullSound, number> = { 'gull-herring-longcall': 11.7, 'gull-common-longcall': 9.3 };

type XZ = readonly [x: number, z: number];
export interface Waters { river: readonly XZ[]; harbour: readonly XZ[] }
/** Where gulls call, per city (engine-local metres: x east, z south). Turku: the Aura's centreline from the Halinen
 *  rapids to its mouth, and the harbour basin Linnanaukko (the ferry terminals and the port's quays), simplified to
 *  12 m and 45 m from OpenStreetMap (ways named Aurajoki, the bay Linnanaukko; © OpenStreetMap contributors, ODbL). */
export const WATERS: Partial<Record<string, Waters>> = {
  turku: {
    river: [[2198, -1569], [2145, -1537], [2011, -1672], [1912, -1735], [1843, -1750], [1766, -1731], [1619, -1637], [1559, -1623],
      [1357, -1643], [1257, -1607], [1143, -1506], [960, -1277], [836, -1113], [764, -982], [711, -538], [600, -196], [432, 133],
      [292, 231], [-466, 657], [-599, 752], [-1383, 1516], [-1960, 1950]],
    harbour: [[-3325, 1672], [-3235, 1514], [-2941, 1625], [-2788, 1155], [-2665, 1171], [-2593, 748], [-2494, 746], [-2452, 1250],
      [-2530, 1527], [-2482, 1572], [-2639, 1753], [-2190, 1952], [-1982, 1908], [-1924, 2044], [-2228, 2152], [-2562, 2001],
      [-2688, 2102], [-2622, 2219], [-2934, 1987], [-3127, 2171]],
  },
};

const clamp = (v: number) => Math.max(0, Math.min(1, v));

/** The nearest point of a polyline: its position, distance, and arc length from the line's start. */
export function nearestOnLine(line: readonly XZ[], x: number, z: number) {
  let best = { x: line[0][0], z: line[0][1], distance: Infinity, along: 0 }, travelled = 0;
  for (let i = 1; i < line.length; i++) {
    const [ax, az] = line[i - 1], [bx, bz] = line[i], dx = bx - ax, dz = bz - az, length = Math.hypot(dx, dz);
    const t = clamp(((x - ax) * dx + (z - az) * dz) / (length * length || 1));
    const px = ax + t * dx, pz = az + t * dz, distance = Math.hypot(px - x, pz - z);
    if (distance < best.distance) best = { x: px, z: pz, distance, along: travelled + t * length };
    travelled += length;
  }
  return best;
}
/** The point `along` metres from a polyline's start (clamped to its ends) and the unit normal there. */
export function pointAlong(line: readonly XZ[], along: number) {
  let left = Math.max(0, along);
  for (let i = 1; i < line.length; i++) {
    const [ax, az] = line[i - 1], [bx, bz] = line[i], length = Math.hypot(bx - ax, bz - az);
    if (left <= length || i === line.length - 1) {
      const t = clamp(left / (length || 1));
      return { x: ax + t * (bx - ax), z: az + t * (bz - az), nx: -(bz - az) / (length || 1), nz: (bx - ax) / (length || 1) };
    }
    left -= length;
  }
  return { x: line[0][0], z: line[0][1], nx: 0, nz: 1 };
}
export function insidePolygon(ring: readonly XZ[], x: number, z: number) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, zi] = ring[i], [xj, zj] = ring[j];
    if ((zi > z) !== (zj > z) && x < (xj - xi) * (z - zi) / (zj - zi) + xi) inside = !inside;
  }
  return inside;
}

/** By the river: full within 60 m of the centreline (the water and both banks), nothing beyond 130 m. */
const RIVER_NEAR = 60, RIVER_FAR = 130;
/** In the harbour: full over the basin and within 150 m of its edge (the quays, the ferry terminals and their
 *  forecourts), nothing beyond 350 m. */
const HARBOUR_NEAR = 150, HARBOUR_FAR = 350;

/** How much the listener is where gulls call: 0 (away from the water) to 1, and which water it is. */
export function gullPresence(position: V3, city: string = CITY.name): { presence: number; where: 'river' | 'harbour' | null } {
  const waters = WATERS[city];
  if (!waters) return { presence: 0, where: null };
  const river = clamp((RIVER_FAR - nearestOnLine(waters.river, position.x, position.z).distance) / (RIVER_FAR - RIVER_NEAR));
  const edge = nearestOnLine([...waters.harbour, waters.harbour[0]], position.x, position.z).distance;
  const harbour = insidePolygon(waters.harbour, position.x, position.z) ? 1 : clamp((HARBOUR_FAR - edge) / (HARBOUR_FAR - HARBOUR_NEAR));
  return harbour >= river ? { presence: harbour, where: harbour > 0 ? 'harbour' : null } : { presence: river, where: 'river' };
}

export interface GullCall { sound: GullSound; position: V3; distance: number; rate: number; where: 'river' | 'harbour' }

/** Seconds from arriving at the water to the first call, and from the end of a call to the next one: gulls call now
 *  and then. The harbour is busier (x 0.7); at the edge of the zone the wait doubles. */
export const FIRST_CALL: readonly [number, number] = [10, 30], CALL_GAP: readonly [number, number] = [45, 120];

export class GullScheduler {
  private nextAt = Infinity;
  private present = false;
  private last: GullSound | null = null;
  constructor(private readonly random: () => number = Math.random) {}

  private between(range: readonly [number, number]) { return range[0] + (range[1] - range[0]) * this.random(); }

  /** Seconds until the next call (Infinity away from the water). */
  wait(now: number) { return this.nextAt - now; }

  /** Call at the mix rate: returns the call that starts now, else null. Away from the water nothing is scheduled. */
  tick(listener: V3, now: number, city: string = CITY.name): GullCall | null {
    const { presence, where } = gullPresence(listener, city), waters = WATERS[city];
    if (!waters || !where || presence <= 0) { this.present = false; this.nextAt = Infinity; return null; }
    if (!this.present) { this.present = true; this.nextAt = now + this.between(FIRST_CALL); return null; }
    if (now < this.nextAt) return null;
    // Mostly the other call than last time, so the two alternate without a fixed pattern.
    const other = GULL_SOUNDS.find(s => s !== this.last)!, sound = this.last && this.random() < 0.75 ? other : GULL_SOUNDS[this.random() < 0.5 ? 0 : 1];
    this.last = sound;
    this.nextAt = now + SECONDS[sound] + this.between(CALL_GAP) * (where === 'harbour' ? 0.7 : 1) * (2 - presence);
    const position = where === 'harbour' ? this.overHarbour(waters, listener) : this.overRiver(waters, listener);
    return { sound, position, where, rate: 0.95 + 0.1 * this.random(),
      distance: Math.hypot(position.x - listener.x, position.y - listener.y, position.z - listener.z) };
  }

  /** A bird over the river 40-170 m up or down stream of the listener, within 30 m of the centreline, 12-35 m up. */
  private overRiver(waters: Waters, listener: V3): V3 {
    const at = nearestOnLine(waters.river, listener.x, listener.z);
    const p = pointAlong(waters.river, at.along + (this.random() < 0.5 ? -1 : 1) * (40 + 130 * this.random()));
    const side = (this.random() * 2 - 1) * 30;
    return { x: p.x + p.nx * side, y: 12 + 23 * this.random(), z: p.z + p.nz * side };
  }

  /** A bird over the basin 60-260 m from the listener; from outside, over the nearest stretch of its edge. */
  private overHarbour(waters: Waters, listener: V3): V3 {
    for (let i = 0; i < 12; i++) {
      const r = 60 + 200 * this.random(), a = 2 * Math.PI * this.random(), x = listener.x + r * Math.cos(a), z = listener.z + r * Math.sin(a);
      if (insidePolygon(waters.harbour, x, z)) return { x, y: 12 + 23 * this.random(), z };
    }
    const edge = nearestOnLine([...waters.harbour, waters.harbour[0]], listener.x, listener.z);
    return { x: edge.x, y: 12 + 23 * this.random(), z: edge.z };
  }
}

/** Air absorption as a low-pass: about 10 kHz at 50 m, 5 kHz at 150 m, 3 kHz at 300 m. */
export const airCutoff = (metres: number) => 20000 / (1 + Math.max(0, metres) / 55);
/** The share of a call that arrives as echo from the quays and the far bank grows with distance. */
export const echoShare = (metres: number) => Math.min(0.85, 0.12 + Math.max(0, metres) / 260);
/** Delay (s) of the reflection off the water for a bird `height` m up heard from 2 m above the surface. */
export const waterReflection = (metres: number, height: number) =>
  (Math.hypot(metres, height + 2) - Math.hypot(metres, Math.max(0, height - 2))) / 343;

/** Open water between stone quays: a short diffuse tail (1.3 s) and three discrete echoes (the near quay 35 ms, the
 *  far bank 0.19 s, a warehouse wall 0.41 s). Deterministic. */
export function quayEcho(ctx: BaseAudioContext): AudioBuffer {
  const sr = ctx.sampleRate, length = Math.round(1.5 * sr), buffer = ctx.createBuffer(2, length, sr);
  let seed = 0x9e3779b9;
  const noise = () => { seed ^= seed << 13; seed >>>= 0; seed ^= seed >>> 17; seed ^= seed << 5; seed >>>= 0; return seed / 2147483648 - 1; };
  for (let ch = 0; ch < 2; ch++) {
    const data = buffer.getChannelData(ch);
    let low = 0;
    for (let i = 0; i < length; i++) {
      const t = i / sr;
      low += (noise() - low) * (0.5 * Math.exp(-t * 2.5) + 0.08); // the tail darkens as it decays
      data[i] = low * Math.exp(-6.9 * t / 1.3) * Math.min(1, t / 0.012) * 0.5;
    }
    for (const [delay, gain] of [[0.035, 0.5], [0.19, 0.42], [0.41, 0.22]]) data[Math.round((delay + 0.0007 * ch) * sr)] += gain;
  }
  return buffer;
}

export interface GullHost {
  context(): AudioContext | null;
  bus(): AudioNode | null;
  fetch: typeof fetch;
  base: string;
  /** No voice is free (the shared 24-voice budget). */
  full(): boolean;
  error(message: string): void;
}

/** A call's level before distance: the files peak at -3 dBFS, and the panner halves it at twice REFERENCE metres. */
const CALL_GAIN = 1, REFERENCE = 30;
/** The files are fetched once the listener is this close to where gulls call (m beyond the zone's edge). */
const PREFETCH = 250;

export class GullVoices {
  private readonly scheduler: GullScheduler;
  private readonly buffers = new Map<GullSound, AudioBuffer>();
  private loading: Promise<void> | null = null;
  private echo: ConvolverNode | null = null;
  private voice: { source: AudioBufferSourceNode; level: GainNode; nodes: AudioNode[]; call: GullCall } | null = null;
  private paused = false;
  private disposed = false;
  private calls = 0;
  private lastCall: GullCall | null = null;
  private presence = 0;

  constructor(private readonly host: GullHost, random: () => number = Math.random) { this.scheduler = new GullScheduler(random); }

  /** Once per mix tick: fetches the calls near the water, starts the one that is due, follows pause. */
  update(listener: V3, now: number, paused: boolean) {
    if (this.disposed) return;
    const ctx = this.host.context();
    if (paused !== this.paused) { this.paused = paused; if (this.voice && ctx) glide(this.voice.level.gain, paused ? 0 : CALL_GAIN, ctx.currentTime, 0.08); }
    this.presence = gullPresence(listener).presence;
    if (!ctx || !WATERS[CITY.name]) return;
    if (!this.loading && (this.presence > 0 || this.nearWater(listener))) this.load(ctx);
    if (this.buffers.size < GULL_SOUNDS.length || paused || ctx.state !== 'running') return;
    const call = this.scheduler.tick(listener, now);
    if (call && !this.voice && !this.host.full()) this.play(ctx, call);
  }

  private nearWater(p: V3) {
    const waters = WATERS[CITY.name]!;
    return nearestOnLine(waters.river, p.x, p.z).distance < RIVER_FAR + PREFETCH
      || nearestOnLine([...waters.harbour, waters.harbour[0]], p.x, p.z).distance < HARBOUR_FAR + PREFETCH;
  }

  private load(ctx: AudioContext) {
    this.loading = Promise.all(GULL_SOUNDS.map(async sound => {
      try {
        const response = await this.host.fetch(`${this.host.base}${sound}.ogg`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const buffer = await ctx.decodeAudioData(await response.arrayBuffer());
        if (!this.disposed) this.buffers.set(sound, buffer);
      } catch (error) { this.host.error(`${sound}: ${String(error)}`); }
    })).then(() => {});
  }

  private play(ctx: AudioContext, call: GullCall) {
    const bus = this.host.bus(), buffer = this.buffers.get(call.sound);
    if (!bus || !buffer) return;
    try {
      const source = ctx.createBufferSource(), air = ctx.createBiquadFilter(), level = ctx.createGain(), panner = ctx.createPanner();
      source.buffer = buffer; source.playbackRate.value = call.rate;
      air.type = 'lowpass'; air.frequency.value = airCutoff(call.distance); air.Q.value = 0.5;
      level.gain.value = this.paused ? 0 : CALL_GAIN;
      panner.panningModel = 'HRTF'; panner.distanceModel = 'inverse'; panner.refDistance = REFERENCE; panner.maxDistance = 600; panner.rolloffFactor = 1;
      if (panner.positionX) { panner.positionX.value = call.position.x; panner.positionY.value = call.position.y; panner.positionZ.value = call.position.z; }
      else panner.setPosition(call.position.x, call.position.y, call.position.z);
      const nodes: AudioNode[] = [air, level, panner];
      const share = echoShare(call.distance), dry = ctx.createGain();
      dry.gain.value = 1 - 0.5 * share;
      source.connect(air); air.connect(level); level.connect(dry); dry.connect(panner); panner.connect(bus);
      nodes.push(dry);
      // The reflection off the water arrives a few milliseconds late: a comb that closes up with distance.
      if (typeof ctx.createDelay === 'function') {
        const bounce = ctx.createDelay(0.05), bounced = ctx.createGain();
        bounce.delayTime.value = Math.min(0.05, waterReflection(call.distance, call.position.y)); bounced.gain.value = 0.5;
        level.connect(bounce); bounce.connect(bounced); bounced.connect(panner);
        nodes.push(bounce, bounced);
      }
      // The echo over the water comes from all around: it bypasses the panner at the level the distance leaves.
      if (typeof ctx.createConvolver === 'function' && typeof ctx.createBuffer === 'function') {
        if (!this.echo) { this.echo = ctx.createConvolver(); this.echo.buffer = quayEcho(ctx); this.echo.connect(bus); }
        const wet = ctx.createGain();
        wet.gain.value = share * 1.4 * REFERENCE / Math.max(REFERENCE, call.distance);
        level.connect(wet); wet.connect(this.echo);
        nodes.push(wet);
      }
      const voice = this.voice = { source, level, nodes, call };
      source.onended = () => { source.disconnect(); for (const n of nodes) n.disconnect(); if (this.voice === voice) this.voice = null; };
      source.start();
      this.calls++; this.lastCall = call;
    } catch (error) { this.host.error(`gull call: ${String(error)}`); }
  }

  /** Voices in use (the shared budget). */
  get size() { return this.voice ? 1 : 0; }

  snapshot(now?: number) {
    const c = this.lastCall, wait = now === undefined ? null : this.scheduler.wait(now);
    return { presence: +this.presence.toFixed(2), loaded: this.buffers.size, calls: this.calls, playing: this.voice?.call.sound ?? null,
      nextIn: wait === null || !Number.isFinite(wait) ? null : +Math.max(0, wait).toFixed(1),
      last: c ? { sound: c.sound, where: c.where, distance: Math.round(c.distance), position: [Math.round(c.position.x), Math.round(c.position.y), Math.round(c.position.z)],
        cutoff: Math.round(airCutoff(c.distance)), echo: +echoShare(c.distance).toFixed(2), rate: +c.rate.toFixed(3) } : null };
  }

  dispose() {
    this.disposed = true;
    if (this.voice) { try { this.voice.source.stop(); } catch { /* not started */ } this.voice.source.disconnect(); for (const n of this.voice.nodes) n.disconnect(); this.voice = null; }
    this.echo?.disconnect(); this.echo = null; this.buffers.clear();
  }
}
