// The generator's per-tile light list (manifest field `lights`, pipeline/turku3d/props/lights.py): street-light
// posts with their heads, lights hung over a carriageway and facade lights. The client draws a corona for every
// light and a lamp post for every post record; where lamps stand is decided by the generator from the city register.
//
// Records are little-endian, 16 B each. `count` light records come first, then `posts` post records; post i
// belongs to light i. Street lights (flag set) are sorted first and `street` counts them.
//   light: float32 x, y, z (engine metres); uint8 r, g, b = linear colour x intensity / LIGHT_SCALE * 255;
//          uint8 info = category (bits 0-3) | street-light flag (bit 7)
//   post:  float32 x, y, z (post base); uint16 yaw (/65536 * 2pi, three.js rotation.y); uint8 scale * 100; uint8 style
import { VISUAL_L0 } from '../../core/origin';
import type { Lamp, SpriteLight } from './lampField';

export interface TileLightsField {
  format: 'turku3d-lights'; version: number; count: number; posts: number; street: number; data: string;
}

const LIGHT_SCALE = 8;
const CATEGORIES = ['street', 'path', 'hung', 'wall'] as const;
type LightCategory = typeof CATEGORIES[number];
/** Corona radius per category, metres. */
export const CORONA_RADIUS: Record<LightCategory, number> = { street: 1.2, path: 1.0, hung: 1.2, wall: 0.45 };
const RECORD = 16;

type Bounds = readonly [number, number, number, number];

export function tileBounds(id: string): Bounds | null {
  const m = /^t0_(-?\d+)_(-?\d+)$/.exec(id);
  if (!m) return null;
  const i = Number(m[1]), j = Number(m[2]);
  return [i * VISUAL_L0, j * VISUAL_L0, (i + 1) * VISUAL_L0, (j + 1) * VISUAL_L0];
}

/** Half-open ownership: a point on a shared seam belongs to exactly one tile. */
export const owns = (b: Bounds, x: number, z: number) => x >= b[0] && x < b[2] && z >= b[1] && z < b[3];

export const lampKey = (x: number, z: number) => `${Math.round(x * 10)},${Math.round(z * 10)}`;

function bytes(base64: string): Uint8Array {
  const text = atob(base64);
  const out = new Uint8Array(text.length);
  for (let i = 0; i < text.length; i++) out[i] = text.charCodeAt(i);
  return out;
}

/** Content identity of a light list: unchanged on every re-poll of the same tile build. */
export const lightsSignature = (field: TileLightsField | null | undefined) =>
  field ? `${field.count}/${field.posts}/${field.data}` : '';

/** The tile's lamps: one per light record, with its post where it has one. Malformed lists decode to nothing. */
export function decodeTileLights(field: TileLightsField | null | undefined): Lamp[] {
  if (!field || field.format !== 'turku3d-lights' || field.version !== 1) return [];
  const data = bytes(field.data);
  if (data.length !== (field.count + field.posts) * RECORD || field.posts > field.count) return [];
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const lamps: Lamp[] = [];
  for (let i = 0; i < field.count; i++) {
    const o = i * RECORD;
    const x = view.getFloat32(o, true), y = view.getFloat32(o + 4, true), z = view.getFloat32(o + 8, true);
    const info = view.getUint8(o + 15);
    const category = CATEGORIES[info & 0x0f] ?? 'street';
    const k = LIGHT_SCALE / 255;
    const light: SpriteLight = { x, y, z, radius: CORONA_RADIUS[category],
      r: view.getUint8(o + 12) * k, g: view.getUint8(o + 13) * k, b: view.getUint8(o + 14) * k, day: 0 };
    // Facade lights are building lights; posts and lamps hung over the street are street lights (lamp pools).
    const kind = category === 'wall' ? 'wall' : 'street';
    if (i < field.posts) {
      const p = (field.count + i) * RECORD;
      const bx = view.getFloat32(p, true), by = view.getFloat32(p + 4, true), bz = view.getFloat32(p + 8, true);
      const yaw = view.getUint16(p + 12, true) / 65536 * Math.PI * 2;
      lamps.push({ key: lampKey(bx, bz), x: bx, y: by, z: bz, yaw, scale: view.getUint8(p + 14) / 100, post: true, kind, light });
    } else {
      lamps.push({ key: lampKey(x, z), x, y, z, yaw: 0, scale: 1, post: false, kind, light });
    }
  }
  return lamps;
}
