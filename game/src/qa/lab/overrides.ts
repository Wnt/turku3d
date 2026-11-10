// Bench lab LOD and distance overrides (`?hd=&props=&trees=&shadow=&cascades=&fullr=&cells=&reflect=&cars=&lodfade=`). Each
// sets an existing engine constant before anything reads it, so the engine runs its normal code path with another
// value; absent parameters leave the constants untouched. `lodSettings` reads back what the engine now uses.
import { QUALITY, type QualityTier } from '../../render/quality';
import { BLOCK_LOD } from '../../world/buildingPlacements';
import { PROP_LOD } from '../../world/propLod';
import { TILE_WINDOW } from '../../world/coarseRegion';
import { SCENERY_CELLS, TILE_DETAIL, detailFloor } from '../../world/sceneryView';
import { REFLECTION_RANGE } from '../../render/reflections';
import { LOD_FADE } from '../../render/lodFade';
import { VEGETATION_FADE, scaleVegetationFade } from '../../world/nature/materials';
import { TRAFFIC_CAPS } from '../../traffic/constants';
import { walkerCap } from '../../world/walkers';
import { VISUAL_L0 } from '../../core/origin';
import { canopyClear } from '../../render/sceneryMaterial';
import type { LodOverrides } from './params';

/** Props show again this much closer than they hide (the engine's 125/115 m hysteresis). */
const PROP_BAND = PROP_LOD.hide - PROP_LOD.show;
/** The first cascade of a generated split set, as a fraction of the far distance (medium 16/200, high 12/240). */
const FIRST_SPLIT = 0.07;
/** A full tile yields to its scenery cell beyond TILE_DETAIL.distance; it must cover the whole window on the ground
 *  (the 3x3's farthest tile is 354 m away, 450 m with the margin the engine uses). */
const DETAIL_MARGIN = 96;

/** Cascade splits (m) for a far distance and count: the tier's own ratios when the count is unchanged, else
 *  logarithmic splits from FIRST_SPLIT * far. */
export function shadowSplits(current: readonly number[], far: number | null, cascades: number | null): number[] {
  const n = cascades ?? current.length, f = far ?? current[current.length - 1];
  const r1 = (v: number) => Math.round(v * 10) / 10;
  if (n === current.length) return current.map(d => r1(d * f / current[current.length - 1]));
  if (n === 1) return [r1(f)];
  return Array.from({ length: n }, (_, i) => r1(f * FIRST_SPLIT ** ((n - 1 - i) / (n - 1))));
}

/** The TILE_DETAIL distance that keeps a (2r+1)^2 window of full tiles drawn for a camera on the ground. */
export function tileDetailFor(radius: number, current: number) {
  return Math.max(current, Math.round(radius * VISUAL_L0 * Math.SQRT2 + DETAIL_MARGIN));
}

/** Writable numbers in place of the `as const` literal types. */
type Mutable<T> = { -readonly [K in keyof T]: T[K] extends number ? number : T[K] };

/** Set the overridden engine constants; call before the environment, tiles and vegetation are built. */
export function applyLodOverrides(lod: LodOverrides, tier: QualityTier, params: { traffic: number | null }) {
  const q = QUALITY[tier] as unknown as { vegetation: { near: number; mid: number; far: number }; splits: number[] };
  if (lod.hd !== null) BLOCK_LOD.hdDistance = lod.hd;
  if (lod.props !== null) Object.assign(PROP_LOD as Mutable<typeof PROP_LOD>, { hide: lod.props, show: Math.max(0, lod.props - PROP_BAND) });
  if (lod.trees) [q.vegetation.near, q.vegetation.mid, q.vegetation.far] = lod.trees;
  if (lod.shadow !== null || lod.cascades !== null) q.splits = shadowSplits(q.splits, lod.shadow, lod.cascades);
  if (lod.fullr !== null) {
    TILE_WINDOW.radius = lod.fullr;
    (TILE_DETAIL as Mutable<typeof TILE_DETAIL>).distance = tileDetailFor(lod.fullr, TILE_DETAIL.distance);
    canopyClear.value = TILE_DETAIL.distance;
  }
  if (lod.cells !== null) SCENERY_CELLS.distance = lod.cells;
  if (lod.reflect !== null) REFLECTION_RANGE.maxDistance = lod.reflect;
  if (lod.cars !== null) params.traffic = lod.cars;
  if (lod.lodfade !== null) LOD_FADE.seconds = lod.lodfade;
  if (lod.treefade !== null) scaleVegetationFade(lod.treefade);
}

/** The LOD distances and caps the engine runs with now, for the result. */
export function lodSettings(tier: QualityTier, params: { traffic: number | null; walkers: number | null }) {
  const q = QUALITY[tier];
  return {
    hd: BLOCK_LOD.hdDistance ?? 'per block (tile data, 280 m)',
    props: { hide: PROP_LOD.hide, show: PROP_LOD.show },
    trees: [q.vegetation.near, q.vegetation.mid, q.vegetation.far],
    shadow: { far: q.splits[q.splits.length - 1], cascades: q.splits.length, splits: [...q.splits] },
    fullr: { radius: TILE_WINDOW.radius, window: `${2 * TILE_WINDOW.radius + 1}x${2 * TILE_WINDOW.radius + 1}`, tileDetail: TILE_DETAIL.distance },
    cells: SCENERY_CELLS.distance ?? 'pixel rule (350..2000 m)',
    cellFloor: detailFloor(),
    reflect: Number.isFinite(REFLECTION_RANGE.maxDistance) ? REFLECTION_RANGE.maxDistance : 'unlimited (water coverage only)',
    cars: params.traffic ?? TRAFFIC_CAPS[tier],
    walkers: walkerCap(tier, params.walkers),
    lodfade: LOD_FADE.seconds,
    treefade: { width: [...VEGETATION_FADE.width], stagger: [...VEGETATION_FADE.stagger] },
  };
}
