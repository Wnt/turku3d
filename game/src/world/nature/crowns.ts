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
/** The lowest crown base (fraction of the height) a measured tree takes, and the most its crown stretches down. */
export const CROWN_BASE_LIMITS = { min: 0.05, stretch: 1.3 } as const;
/**
 * Vertical stretch about the tree top that brings the model's crown base (`modelBase`, crownShape's 3rd-percentile leaf
 * height as a fraction of the height) down to the measured one (`measured`, trees-lidar@2: the median 10th-percentile
 * return height of the 2 m leaf cells on the crown's open side, as a fraction of the height). On solitary park trees
 * the measurement gives 0.29-0.31, the models' 0.27-0.35; stand edges and roadside rows 0.16-0.26 (Kurala,
 * Puolanpuisto, a central park). 1 where nothing was measured or the measured base is not lower: the foot would lift off.
 */
export function crownBaseStretch(modelBase: number | undefined, measured: number | null | undefined): number {
  if (modelBase === undefined || measured === null || measured === undefined || !(measured < modelBase)) return 1;
  const base = Math.max(CROWN_BASE_LIMITS.min, measured);
  return Math.min(CROWN_BASE_LIMITS.stretch, (1 - base) / (1 - modelBase));
}

/** Widening factor limits per fifth, and the core around the trunk that keeps its thickness (fraction of the radius). */
export const CROWN_LIMITS = { min: 0.8, max: 1.6, core: [0.04, 0.14] as const, below: 0.12 };

/**
 * How the crowns widen, on every tier: every vertex widens, so leaf cards grow with the crown (as full as the lidar
 * crown, about 1.3x the leaf fragments). Jonni's pick (2026-11-05, treelook2, live A/B after the TRAA fix e156e3ba)
 * over `spread` (cards moved out at their own size). Beauty cost against spread: none measurable on the M1 Max
 * (medium), +0.1 ms on the Air and +1.1 ms on the Radeon 560X (both at medium, 2026-11-04).
 */
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

/**
 * Reshape every LOD of a tree asset with the full LOD's crown fit, so the levels keep the relation lodblend calibrated
 * (alpha coverage, cross-fades) and the shadow proxies match. The impostor stays: its views were baked from the
 * fitted full LOD (scripts/nature/octahedral.mjs fitTree) and its quad only bounds them. Returns the asset's new
 * horizontal extent (m at scale 1), or null when it is not a fitted tree. Attributes shared between parts are
 * reshaped once.
 */
export function fitCrown(asset: NatureAsset, done = new WeakSet<object>()): number | null {
  if (asset.kind !== 'tree') return null;
  const shape = crownShape(asset);
  if (!shape) return null;
  const widening = crownWidening(asset, shape);
  asset.crownBase = shape.base;
  let extent = 0;
  asset.lods.forEach((lod, level) => {
    const impostor = level === asset.lods.length - 1 && level > 0;
    for (const part of lod.parts) {
      const position = part.geometry.getAttribute('position') as THREE.BufferAttribute;
      if (!impostor && !done.has(position)) {
        done.add(position);
        for (let i = 0; i < position.count; i++) {
          const x = position.getX(i), y = position.getY(i), z = position.getZ(i);
          const k = crownFactor(y / asset.height, Math.hypot(x, z) / asset.radius, shape, widening);
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
