// Crown fill: every library tree is reshaped once, at load, so its foliage fills the crown the city lidar measures.
//
// Tiles scale each tree to its measured height and crown radius (natureScale: the radius maps to the asset's widest
// extent). The lidar's crown is much fuller than the models' foliage at that size. Per fifth of the crown height
// (crown base to top), the 90th-percentile horizontal distance of the returns from the trunk is about the measured
// crown radius, against 0.70-0.80 of it for the models' leaf cards: about half the crown volume.
// The probe (loops/lidartrees, tools/crowns_probe.py) ran on 2026-11-04 over 5 tiles: river park t0_1_0 and t0_0_0,
// park t0_0_1, residential t0_-3_2. It counted 582 measured register, OSM and lidar trees, with watershed crowns on a
// 0.5 m canopy height model of classes 3-5.
// The crown base (5th-percentile return height) is 0.25-0.34 of the top, as in the models, so only the width changes.
import type * as THREE from 'three/webgpu';
import type { NatureAsset } from './library';

/** Lidar crown profile: 90th-percentile radius from the trunk per fifth of the crown (bottom to top), in units of the
 *  tile's measured crown radius. Medians of the measured trees of each genus; `broadleaf` is all of them. */
export const LIDAR_CROWNS: Record<string, readonly number[]> = {
  linden: [1.04, 1.08, 1.07, 1.06, 0.92], // 111 trees
  maple: [1.01, 1.03, 0.99, 0.96, 0.77], // 40
  oak: [0.96, 0.96, 0.95, 0.81, 0.66], // 17, with elms (drawn as oaks)
  broadleaf: [0.99, 1.0, 0.97, 0.93, 0.79], // 582
};
/** Conifers keep their model's taper (too few surveyed in the probe); their widest fifth reaches the measured radius. */
export const CONIFER_WIDEST = 1.0;
const CONIFERS = new Set(['pine', 'spruce']);
/** Widening factor limits per fifth, and the core around the trunk that keeps its thickness (fraction of the radius). */
export const CROWN_LIMITS = { min: 0.8, max: 1.6, core: [0.04, 0.14] as const, below: 0.12 };

/**
 * How the crowns widen, by quality tier. `stretch` (medium, high) widens every vertex, so leaf cards grow with the
 * crown: as full as the lidar crown, about 1.3x the leaf fragments. `spread` (reduced, low, mobile: the fill-rate bound
 * tiers) moves each leaf card out with its centre at its own size: the model's leaf fragments, a more open crown.
 * Decided 2026-11-04 (treelook): blind judges against Street View crops at five tree-rich poses preferred `stretch` on
 * medium (8 of 15 calls against 5, 2 ties; 4 of 5 on zoomed canopy crops), for fuller crowns with less sky and bare
 * branch. Its beauty cost at medium: none measurable on the M1 Max (4.39 against 4.41 ms p50, riverside static, three
 * interleaved runs), +0.1 ms on the Air and +1.1 ms on the Radeon 560X; the Air and the 560X run reduced (spread).
 * That judging predates the TRAA fix (e156e3ba); Jonni re-judges both modes live (treeLook.ts A/B, ?crowns=).
 */
export type CrownMode = 'stretch' | 'spread';
export function crownMode(tier: string): CrownMode {
  return tier === 'medium' || tier === 'high' ? 'stretch' : 'spread';
}

export interface CrownShape {
  /** Crown base and top as fractions of the asset height (3rd and 99.5th percentile leaf vertex heights). */
  base: number; top: number;
  /** 90th-percentile leaf vertex radius per fifth of the crown, in units of the asset radius. */
  profile: number[];
}

function percentile(values: number[], q: number) {
  if (!values.length) return 0;
  const sorted = Float64Array.from(values).sort();
  return sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))];
}

const isLeaves = (part: { material: THREE.Material }) => part.material.name.startsWith('nature-leaves');

/** The foliage shape of an asset's full LOD. */
export function crownShape(asset: NatureAsset): CrownShape | null {
  const heights: number[] = [], radii: number[] = [];
  for (const part of asset.lods[0]?.parts ?? []) {
    if (!isLeaves(part)) continue;
    const position = part.geometry.getAttribute('position');
    for (let i = 0; i < position.count; i++) {
      heights.push(position.getY(i) / asset.height);
      radii.push(Math.hypot(position.getX(i), position.getZ(i)) / asset.radius);
    }
  }
  if (heights.length < 50) return null;
  const base = percentile(heights, 0.03), top = percentile(heights, 0.995);
  const profile = [0, 1, 2, 3, 4].map(k => {
    const lo = base + (top - base) * k / 5, hi = base + (top - base) * (k + 1) / 5;
    return percentile(radii.filter((_, i) => heights[i] >= lo && heights[i] < hi), 0.9);
  });
  return profile.every(r => r > 0) ? { base, top, profile } : null;
}

/** Widening per fifth of the crown that takes the asset's foliage profile to the lidar's, within CROWN_LIMITS. */
export function crownWidening(asset: NatureAsset, shape: CrownShape): number[] {
  const species = asset.species ?? '';
  const clamp = (k: number) => Math.min(CROWN_LIMITS.max, Math.max(CROWN_LIMITS.min, k));
  if (CONIFERS.has(species)) {
    const k = clamp(CONIFER_WIDEST / Math.max(...shape.profile));
    return shape.profile.map(() => k);
  }
  const target = LIDAR_CROWNS[species] ?? LIDAR_CROWNS.broadleaf;
  return shape.profile.map((r, k) => clamp(target[k] / r));
}

const smoothstep = (a: number, b: number, x: number) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };

/** Horizontal widening of a point at relative height `u` and relative radius `rho`: the fifths' factors interpolated
 *  between their centres, faded in over the last `below` of the trunk under the crown base, and none in the trunk core. */
export function crownFactor(u: number, rho: number, shape: CrownShape, widening: readonly number[]) {
  const s = (u - shape.base) / Math.max(1e-6, shape.top - shape.base) * 5 - 0.5;
  const i = Math.max(0, Math.min(3, Math.floor(s))), t = Math.min(1, Math.max(0, s - i));
  const k = s <= 0 ? widening[0] : s >= 4 ? widening[4] : widening[i] * (1 - t) + widening[i + 1] * t;
  const fade = smoothstep(shape.base - CROWN_LIMITS.below, shape.base, u) * smoothstep(CROWN_LIMITS.core[0], CROWN_LIMITS.core[1], rho);
  return 1 + (k - 1) * fade;
}

/** Connected vertex groups (leaf cards) of an indexed or non-indexed triangle list. */
export function cards(geometry: THREE.BufferGeometry): Int32Array {
  const count = geometry.getAttribute('position').count, parent = new Int32Array(count).map((_, i) => i);
  const find = (i: number) => { while (parent[i] !== i) i = parent[i] = parent[parent[i]]; return i; };
  const index = geometry.index;
  const n = index ? index.count : count;
  for (let t = 0; t + 2 < n; t += 3) {
    const a = find(index ? index.getX(t) : t);
    for (const k of [1, 2]) { const b = find(index ? index.getX(t + k) : t + k); if (a !== b) parent[b] = a; }
  }
  for (let i = 0; i < count; i++) parent[i] = find(i);
  return parent;
}

/**
 * Reshape every LOD of a tree asset with the full LOD's crown fit, so the levels keep the relation lodblend calibrated
 * (alpha coverage, cross-fades) and the shadow proxies match. Impostor planes (one quad per side, no rows) widen by
 * the mean factor. In `spread` mode leaf cards move with their centre instead of stretching. Returns the asset's new
 * horizontal extent (m at scale 1), or null when it is not a fitted tree. Attributes shared between parts are
 * reshaped once.
 */
export function fitCrown(asset: NatureAsset, done = new WeakSet<object>(), mode: CrownMode = 'stretch'): number | null {
  if (asset.kind !== 'tree') return null;
  const shape = crownShape(asset);
  if (!shape) return null;
  const widening = crownWidening(asset, shape);
  const mean = widening.reduce((a, b) => a + b, 0) / widening.length;
  let extent = 0;
  asset.lods.forEach((lod, level) => {
    const impostor = level === asset.lods.length - 1 && level > 0;
    for (const part of lod.parts) {
      const position = part.geometry.getAttribute('position') as THREE.BufferAttribute;
      if (!done.has(position)) {
        done.add(position);
        if (mode === 'spread' && !impostor && isLeaves(part)) {
          // Each card's centre widens; the card keeps its size and shape.
          const owner = cards(part.geometry), sums = new Map<number, [number, number, number, number]>();
          for (let i = 0; i < position.count; i++) {
            const sum = sums.get(owner[i]) ?? [0, 0, 0, 0];
            sum[0] += position.getX(i); sum[1] += position.getY(i); sum[2] += position.getZ(i); sum[3]++;
            sums.set(owner[i], sum);
          }
          const shift = new Map<number, [number, number]>();
          for (const [card, [x, y, z, n]] of sums) {
            const k = crownFactor(y / n / asset.height, Math.hypot(x, z) / n / asset.radius, shape, widening);
            shift.set(card, [x / n * (k - 1), z / n * (k - 1)]);
          }
          for (let i = 0; i < position.count; i++) {
            const [dx, dz] = shift.get(owner[i])!;
            position.setXYZ(i, position.getX(i) + dx, position.getY(i), position.getZ(i) + dz);
          }
        } else for (let i = 0; i < position.count; i++) {
          const x = position.getX(i), y = position.getY(i), z = position.getZ(i);
          const k = impostor ? mean : crownFactor(y / asset.height, Math.hypot(x, z) / asset.radius, shape, widening);
          position.setXYZ(i, x * k, y, z * k);
        }
        position.needsUpdate = true;
        part.geometry.computeBoundingSphere();
        part.geometry.boundingBox = null;
      }
      for (let i = 0; i < position.count; i++) extent = Math.max(extent, Math.hypot(position.getX(i), position.getZ(i)));
    }
  });
  return extent;
}
