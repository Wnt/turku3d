import type { V3 } from '../core/origin';
import type { VehicleKind } from '../physics/constants';
import { MATERIALS } from '../shared/col/materials';
import type { TyreFrame } from './engine/tyre';
import type { EngineVoiceState } from './engine/voices';
import { MIX_HZ } from './automation';
import { WATERS, nearestOnLine } from './gulls';
import { CITY } from '../core/city';

export const SOUNDS = ['step-asphalt', 'step-stone', 'step-grass', 'step-gravel', 'step-wood',
  'engine', 'scooter', 'curb', 'impact', 'door-open', 'door-close',
  'city', 'river', 'traffic', 'wind', 'connect', 'crowd', 'crunch', 'scrape', 'glass-break', 'rattle', 'hiss'] as const;
export type Sound = typeof SOUNDS[number];
export type Channel = 'master' | 'footsteps' | 'vehicle' | 'ambience' | 'voice' | 'ui';
export const clamp = (v: number, max = 1) => Math.max(0, Math.min(max, Number.isFinite(v) ? v : 0));

export function footstep(material: number): Sound | null {
  const surface = MATERIALS[material]?.audio ?? 'asphalt';
  if (surface === 'none' || surface === 'water') return null;
  if (surface === 'grass' || surface === 'soil') return 'step-grass';
  if (surface === 'gravel' || surface === 'sand') return 'step-gravel';
  if (surface === 'wood') return 'step-wood';
  if (surface === 'asphalt') return 'step-asphalt';
  return 'step-stone';
}

/** The beds by place: the river's water sounds from the nearest point of the city's river centreline (gulls.ts
 *  WATERS, the Aura in Turku; a city without one has no river bed) and fades to zero over 140 m. */
export function ambience(position: V3) {
  const line = WATERS[CITY.name]?.river, at = line ? nearestOnLine(line, position.x, position.z) : null;
  const near = at ? clamp(1 - at.distance / 140) : 0;
  return { river: at ? { x: at.x, y: 0, z: at.z } : { x: position.x, y: 0, z: position.z }, water: near * 0.32,
    city: 0.16 - near * 0.07, traffic: 0.12 - near * 0.04, wind: 0.06 + near * 0.04 };
}

/** Crowd murmur bed: silent with nobody around, full with a dozen drawn walkers within 40 m (world/walkers). */
export const CROWD_RADIUS = 40;
export const crowdGain = (walkers: number) => clamp(walkers / 12) * 0.13;

/** The trophy truck's engine, tyres and chassis are synthesised at runtime (audio/engine), not a sample loop. */
export const SYNTH_ENGINE = 'v8-synth';
/** Engine sound per vehicle class: the car's low four-stroke rumble and the scooter's buzzing single cylinder as
 *  sample loops; the trophy truck's NA crossplane V8 from the engine voice pool (GameAudio.engineVoice). */
export const ENGINE_SOUND = { car: 'engine', scooter: 'scooter', truck: SYNTH_ENGINE } as const satisfies Record<VehicleKind, Sound | typeof SYNTH_ENGINE>;
/** Vehicle classes whose engine is a sample loop and whose tyres have their own synthesised voice (engine/tyreVoices.ts). */
export type LoopVehicle = Exclude<VehicleKind, 'truck'>;
export const isLoopVehicle = (kind: VehicleKind): kind is LoopVehicle => ENGINE_SOUND[kind] !== SYNTH_ENGINE;

/** Per sample-loop class: playback rate at 0 rpm and its rise up to `rpmTop`, engine gain at idle and its rise with
 *  throttle. The scooter's small engine idles and revs higher. */
const ENGINE_MIX = {
  car: { rate: 0.65, rise: 2, rpmTop: 6000, idle: 0.12, throttle: 0.23 },
  scooter: { rate: 0.8, rise: 1.6, rpmTop: 9000, idle: 0.1, throttle: 0.2 },
} as const satisfies Record<LoopVehicle, object>;

/** Engine loop of a sample-loop vehicle; the synthesised truck gets silence here (its voice carries everything). */
export function carMix(rpm: number, throttle: number, running: boolean, kind: VehicleKind = 'car') {
  if (!isLoopVehicle(kind)) return { rate: 1, engine: 0 };
  const m = ENGINE_MIX[kind];
  return { rate: m.rate + clamp(rpm / m.rpmTop) * m.rise, engine: running ? m.idle + clamp(throttle) * m.throttle : 0 };
}

/** A damaged engine runs rough (vehicle/mechanics.ts `rough`, 0..1): at each continuous-mix tick (MIX_HZ) an
 *  irregular share of the ticks misfires, a one-tick dip of gain and pitch that the 40 ms parameter glide turns into a
 *  stumble, and the revs hunt slowly; both settle as the throttle opens (combustion steadies under load). The pattern
 *  is a function of the tick, so it costs nothing between ticks. Multipliers of the engine loop's gain and rate. */
export function roughEngine(rough: number, throttle: number, time: number): { rate: number; gain: number } {
  const r = clamp(rough) * (1 - .6 * clamp(throttle));
  if (r <= 0 || !Number.isFinite(time)) return { rate: 1, gain: 1 };
  let h = Math.imul(Math.floor(time * MIX_HZ) ^ 0x5bd1e995, 0x27d4eb2d);
  h = Math.imul(h ^ h >>> 15, 0x165667b1); h ^= h >>> 13;
  const misfire = (h >>> 0) / 4294967296 < .3 * r, hunt = Math.sin(time * 2 * Math.PI * 1.3);
  return { rate: (1 + .03 * r * hunt) * (misfire ? 1 - .06 * r : 1), gain: misfire ? 1 - .55 * r : 1 };
}

/** A damaging hit's crunch: from a 12 km/h knock to full at 80 km/h. */
export const crunchGain = (kmh: number) => clamp((kmh - 12) / 68) * 0.8;
/** Damage loops of the driven car: the scrape grinds from 1.5 m/s of sliding (louder and higher as it speeds up), loose
 *  parts rattle with the car's speed (more with more of them), a holed radiator hisses while it steams. */
export function damageLoops(scrape: number, loose: number, steam: number, speed: number) {
  const s = clamp((scrape - 1.5) / 10);
  return { scrape: s * 0.42, scrapeRate: 0.8 + s * 0.5, rattle: clamp(loose / 2) * clamp(Math.abs(speed) / 12) * 0.16, hiss: clamp(steam) * 0.2 };
}

/** Engine voices for ambient traffic (traffic/client.ts): at most this many, for the nearest moving cars. */
export const TRAFFIC_VOICES = 8;

/** A passing car's engine: quieter than the player's, pitch rising with speed (city speeds, 0..14 m/s). */
export function trafficMix(speed: number) {
  const s = clamp(speed / 14);
  return { rate: 0.62 + s * 0.85, engine: 0.05 + s * 0.11 };
}

/** The street traffic bed follows how many cars are within 150 m, instead of a fixed loop. */
export const trafficBed = (count: number) => clamp(count / 10);

export type HornEmitter = { id: string; kind: VehicleKind; position: V3 };
export type AudioEvent =
  | { type: 'horns'; vehicles: HornEmitter[] }
  | { type: 'footstep'; material: number; position: V3; speed: number }
  | { type: 'curb' | 'impact'; position: V3; impulse: number }
  /** A car's body took a damaging hit (actors/fleet/damageRig.ts 'hit'), `kmh` its speed change; a window shattered. */
  | { type: 'crunch'; position: V3; kmh: number }
  | { type: 'glass'; position: V3 }
  | { type: 'door'; position: V3; open: boolean }
  | { type: 'gamepad-connected' }
  | { type: 'mute' }
  | { type: 'volume'; channel: Channel; value: number }
  | { type: 'listener'; position: V3; forward: V3; up: V3; paused: boolean }
  | { type: 'crowd'; walkers: number }
  /** The local vehicle; `tyres`: its tyre frame (engine/tyre.ts TyreTracker), silent while nobody drives it;
   *  `rough`: a damaged engine's roughness (vehicle/mechanics.ts), absent or 0 when intact. */
  | { type: 'car'; kind: VehicleKind; position: V3; rpm: number; throttle: number; speed: number; tyres: TyreFrame; running: boolean; rough?: number;
    /** Damage layers (damageLoops): the body sliding along something (m/s), parts hanging loose, the radiator's steam. */
    scrape?: number; loose?: number; steam?: number }
  /** One frame of a synthesised truck engine voice (GameAudio.engineVoice); `state` null releases it. */
  | { type: 'engine-voice'; id: string; state: EngineVoiceState | null }
  /** Nearest moving ambient cars (stable `id`s) and the count within 150 m. */
  | { type: 'traffic'; cars: { id: number; position: V3; speed: number }[]; count: number; centre: V3 | null };

export function oneShot(event: AudioEvent): { sound: Sound; channel: Channel; gain: number; position?: V3 } | null {
  switch (event.type) {
    case 'footstep': {
      const sound = footstep(event.material);
      return sound ? { sound, channel: 'footsteps', gain: 0.22 + clamp(event.speed / 6) * 0.2, position: event.position } : null;
    }
    case 'curb': case 'impact': return { sound: event.type, channel: 'vehicle', gain: clamp(event.impulse / 6000) * 0.65, position: event.position };
    case 'door': return { sound: event.open ? 'door-open' : 'door-close', channel: 'vehicle', gain: 0.35, position: event.position };
    case 'crunch': return { sound: 'crunch', channel: 'vehicle', gain: crunchGain(event.kmh), position: event.position };
    case 'glass': return { sound: 'glass-break', channel: 'vehicle', gain: 0.5, position: event.position };
    case 'gamepad-connected': return { sound: 'connect', channel: 'ui', gain: 0.25 };
    default: return null;
  }
}

/** Voice lines: a new line fades the previous out over this long, then starts. */
export const VOICE_CUT_SECONDS = 0.1;
/** Ambience is ducked by this much while a voice line plays. */
const VOICE_DUCK_DB = -6;
export const duckGain = (active: boolean) => (active ? 10 ** (VOICE_DUCK_DB / 20) : 1);
