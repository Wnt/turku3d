// Real light pools: the street lamps nearest the camera drive the fixed DynamicLighting point slots
// (Environment.lamps.point). Slots only move and change colour and intensity: the light set, and with it every
// material's cache key and program, never changes (render/lighting.ts). Purely local: the choice depends only on
// this camera, and every client already agrees on the lamps and the night level that scale it.
import type * as THREE from 'three/webgpu';
import type { Lamp, LampKind } from './lampField';

export const POOL = {
  /** Re-pick the nearest lamps this often; slots fade continuously in between. */
  pickMs: 250,
  /** Time for a slot to fade fully in or out, seconds. */
  fade: 0.6,
  /** Lamps are ranked by horizontal distance from a point this far ahead of the camera (metres): a lamp behind the
   *  camera lights little of what it sees. */
  lookAhead: 10,
  /** A chosen lamp's weight falls linearly to 0 as its rank distance approaches the first lamp left out (or `reach`),
   *  so a lamp leaves or joins the set when its pool is already dark: no pop at a hand-over. Metres. */
  margin: 14,
  /** Lamps beyond this rank distance get no slot. */
  reach: 90,
  /** Point light range (r186 cutoff window) and physical inverse-square decay. */
  distance: 32,
  decay: 2,
} as const;

/** Peak point-light intensity per lamp kind at full night. Tunnel strips and facade lights stay sprites only: a
 *  point light half a metre from a wall or a tunnel ceiling is a hot spot, not a pool. */
export const POOL_INTENSITY: Partial<Record<LampKind, number>> = { street: 50, bridge: 40 };

/** One lamp that can light a pool: its light point, linear colour and peak intensity (candela-like, r186 units). */
export interface PoolLamp { key: string; x: number; y: number; z: number; r: number; g: number; b: number; intensity: number }

/** The pool record of a lamp, or null for kinds that light no pool. Colour is the corona's, normalised. */
export function poolLamp(lamp: Lamp): PoolLamp | null {
  const intensity = POOL_INTENSITY[lamp.kind];
  if (!intensity) return null;
  const { x, y, z, r, g, b } = lamp.light;
  const peak = Math.max(r, g, b, 1e-6);
  return { key: lamp.key, x, y, z, r: r / peak, g: g / peak, b: b / peak, intensity };
}

interface Slot { light: THREE.PointLight; lamp: PoolLamp | null; level: number; target: number }

/** Nearest `count + 1` entries by `d2`, ascending (count is a tier's slot count: a handful). */
export function nearest<T>(items: readonly T[], d2: (item: T) => number, count: number): { item: T; d: number }[] {
  const best: { item: T; d: number }[] = [];
  for (const item of items) {
    const d = d2(item);
    if (best.length > count && d >= best[count].d) continue;
    let i = best.length;
    while (i > 0 && best[i - 1].d > d) i--;
    best.splice(i, 0, { item, d });
    if (best.length > count + 1) best.pop();
  }
  for (const b of best) b.d = Math.sqrt(b.d);
  return best;
}

/** Target weights of the chosen lamps by key: 1 well inside the set, 0 at the distance of the first lamp left out. */
export function poolWeights(ranked: readonly { item: PoolLamp; d: number }[], count: number): Map<string, { lamp: PoolLamp; weight: number }> {
  const cut = Math.min(POOL.reach, ranked.length > count ? ranked[count].d : Infinity);
  const weights = new Map<string, { lamp: PoolLamp; weight: number }>();
  for (const { item, d } of ranked.slice(0, count)) {
    const weight = Math.min(1, Math.max(0, (cut - d) / POOL.margin));
    if (weight > 0) weights.set(item.key, { lamp: item, weight });
  }
  return weights;
}

export class LampPools {
  private slots: Slot[];
  private lamps: readonly PoolLamp[] = [];
  /** Chosen lamps by key: a re-streamed tile hands over equal lamps as new objects without a fade. */
  private weights = new Map<string, { lamp: PoolLamp; weight: number }>();
  private nextPick = -Infinity;
  private last = -Infinity;

  constructor(lights: readonly THREE.PointLight[]) {
    this.slots = lights.map(light => {
      light.intensity = 0;
      light.distance = POOL.distance;
      light.decay = POOL.decay;
      return { light, lamp: null, level: 0, target: 0 };
    });
  }

  get size() { return this.slots.length; }

  /** Every resident lamp that can light a pool. */
  setLamps(lamps: readonly PoolLamp[]) { this.lamps = lamps; this.nextPick = -Infinity; }

  /**
   * Per frame. `x`/`z` is the camera, `fx`/`fz` its view direction (any length), `night` the shared night level.
   * Lamps are re-ranked every POOL.pickMs; a slot changes lamp only once it has faded out.
   */
  update(x: number, z: number, fx: number, fz: number, night: number, now = performance.now()) {
    if (!this.slots.length) return;
    const dt = Number.isFinite(this.last) ? Math.min(0.25, Math.max(0, (now - this.last) / 1000)) : 0;
    this.last = now;
    if (now >= this.nextPick) {
      this.nextPick = now + POOL.pickMs;
      this.pick(x, z, fx, fz);
    }
    const step = dt / POOL.fade;
    for (const slot of this.slots) {
      const chosen = slot.lamp ? this.weights.get(slot.lamp.key) : undefined;
      // A re-streamed tile hands over an equal lamp (same key, same place) as a new record.
      if (chosen) slot.lamp = chosen.lamp;
      slot.target = chosen?.weight ?? 0;
      slot.level += Math.max(-step, Math.min(step, slot.target - slot.level));
      if (slot.level <= 0 && slot.target <= 0) { slot.level = 0; slot.lamp = null; }
    }
    // Lamps that want a slot take the free ones, nearest first; a free slot starts dark, so moving it cannot pop.
    for (const [key, { lamp }] of this.weights) {
      if (this.slots.some(s => s.lamp?.key === key)) continue;
      const slot = this.slots.find(s => !s.lamp);
      if (!slot) break;
      slot.lamp = lamp; slot.level = 0;
      slot.light.position.set(lamp.x, lamp.y, lamp.z);
      slot.light.color.setRGB(lamp.r, lamp.g, lamp.b);
    }
    for (const slot of this.slots) {
      const level = slot.lamp ? slot.level * slot.level * (3 - 2 * slot.level) : 0;
      const intensity = (slot.lamp?.intensity ?? 0) * level * night;
      // Only slots above 0 enter the shader loop (lighting.ts): by day there is no per-fragment cost.
      slot.light.intensity = intensity > 1e-3 ? intensity : 0;
    }
  }

  private pick(x: number, z: number, fx: number, fz: number) {
    const h = Math.hypot(fx, fz);
    const ax = h > 1e-6 ? x + fx / h * POOL.lookAhead : x, az = h > 1e-6 ? z + fz / h * POOL.lookAhead : z;
    const count = this.slots.length;
    this.weights = poolWeights(nearest(this.lamps, l => (l.x - ax) ** 2 + (l.z - az) ** 2, count), count);
  }

  /** Lamps currently lighting (for the debug HUD and tests). */
  snapshot() {
    return this.slots.map(s => ({ key: s.lamp?.key ?? null, level: Math.round(s.level * 1000) / 1000, intensity: s.light.intensity }));
  }
}
