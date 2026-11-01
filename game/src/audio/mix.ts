import type { V3 } from '../core/origin';
import type { VehicleKind } from '../physics/constants';
import { MATERIALS } from '../shared/col/materials';

export const SOUNDS = ['step-asphalt', 'step-stone', 'step-grass', 'step-gravel', 'step-wood',
  'engine', 'scooter', 'truck', 'roll', 'screech', 'curb', 'impact', 'door-open', 'door-close',
  'city', 'river', 'gulls', 'traffic', 'wind', 'connect', 'crowd'] as const;
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

// Approximate Aura centreline through the office AOI, engine-local metres. Replace with
// measured water geometry when the world exports a water emitter contract.
const RIVER = [{ x: -150, y: 0, z: 540 }, { x: 200, y: 0, z: 310 }, { x: 540, y: 0, z: 100 }];
export function ambience(position: V3) {
  let distance = Infinity, river = RIVER[0];
  for (let i = 1; i < RIVER.length; i++) {
    const a = RIVER[i - 1], b = RIVER[i], dx = b.x - a.x, dz = b.z - a.z;
    const t = clamp(((position.x - a.x) * dx + (position.z - a.z) * dz) / (dx * dx + dz * dz));
    const p = { x: a.x + t * dx, y: 0, z: a.z + t * dz };
    const d = Math.hypot(p.x - position.x, p.z - position.z);
    if (d < distance) { distance = d; river = p; }
  }
  const near = clamp(1 - distance / 140);
  return { river, water: near * 0.32, gulls: near * 0.16,
    city: 0.16 - near * 0.07, traffic: 0.12 - near * 0.04, wind: 0.06 + near * 0.04 };
}

/** Crowd murmur bed: silent with nobody around, full with a dozen drawn walkers within 40 m (world/walkers). */
export const CROWD_RADIUS = 40;
export const crowdGain = (walkers: number) => clamp(walkers / 12) * 0.13;

/** Engine loop per vehicle class: the car's low four-stroke rumble, the scooter's buzzing single cylinder or the
 *  trophy truck's V6 with its open exhaust. */
export const ENGINE_SOUND = { car: 'engine', scooter: 'scooter', truck: 'truck' } as const satisfies Record<VehicleKind, Sound>;

/** Per class: playback rate at 0 rpm and its rise up to `rpmTop`, engine gain at idle and its rise with throttle,
 *  and the tyre roll gain. The scooter's small engine idles and revs higher with quieter tyres; the truck's V6 is
 *  the loudest layer and its big off-road tyres roar. */
const ENGINE_MIX = {
  car: { rate: 0.65, rise: 2, rpmTop: 6000, idle: 0.12, throttle: 0.23, roll: 0.22 },
  scooter: { rate: 0.8, rise: 1.6, rpmTop: 9000, idle: 0.1, throttle: 0.2, roll: 0.12 },
  truck: { rate: 0.6, rise: 1.7, rpmTop: 6000, idle: 0.2, throttle: 0.35, roll: 0.3 },
} as const satisfies Record<VehicleKind, object>;

export function carMix(rpm: number, throttle: number, speed: number, slip: number, running: boolean, kind: VehicleKind = 'car') {
  const m = ENGINE_MIX[kind];
  return { rate: m.rate + clamp(rpm / m.rpmTop) * m.rise,
    engine: running ? m.idle + clamp(throttle) * m.throttle : 0,
    roll: clamp(Math.abs(speed) / 24) * m.roll,
    screech: clamp((Math.abs(slip) - 0.15) / 0.65) * clamp(Math.abs(speed) / 5) * 0.3 };
}

export type AudioEvent =
  | { type: 'footstep'; material: number; position: V3; speed: number }
  | { type: 'curb' | 'impact'; position: V3; impulse: number }
  | { type: 'door'; position: V3; open: boolean }
  | { type: 'gamepad-connected' }
  | { type: 'mute' }
  | { type: 'volume'; channel: Channel; value: number }
  | { type: 'listener'; position: V3; forward: V3; up: V3; paused: boolean }
  | { type: 'crowd'; walkers: number }
  | { type: 'car'; kind: VehicleKind; position: V3; rpm: number; throttle: number; speed: number; slip: number; running: boolean };

export function oneShot(event: AudioEvent): { sound: Sound; channel: Channel; gain: number; position?: V3 } | null {
  switch (event.type) {
    case 'footstep': {
      const sound = footstep(event.material);
      return sound ? { sound, channel: 'footsteps', gain: 0.22 + clamp(event.speed / 6) * 0.2, position: event.position } : null;
    }
    case 'curb': case 'impact': return { sound: event.type, channel: 'vehicle', gain: clamp(event.impulse / 6000) * 0.65, position: event.position };
    case 'door': return { sound: event.open ? 'door-open' : 'door-close', channel: 'vehicle', gain: 0.35, position: event.position };
    case 'gamepad-connected': return { sound: 'connect', channel: 'ui', gain: 0.25 };
    default: return null;
  }
}

/** Voice lines: a new line fades the previous out over this long, then starts. */
export const VOICE_CUT_SECONDS = 0.1;
/** Ambience is ducked by this much while a voice line plays. */
const VOICE_DUCK_DB = -6;
export const duckGain = (active: boolean) => (active ? 10 ** (VOICE_DUCK_DB / 20) : 1);
