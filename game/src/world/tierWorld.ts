// A quality tier's draw distances and streaming concurrency (render/quality.ts WORLD_TIERS), applied to the engine
// constants the streamer, the building bands, the props and the scenery quadtree read every scan: at startup before
// the bench lab's overrides (qa/lab/overrides.ts sets the same constants), and again when the pause menu switches the
// preset in a running session.
import { WORLD_TIERS, type QualityTier } from '../render/quality';
import { BLOCK_LOD } from './buildingPlacements';
import { TILE_WINDOW } from './coarseRegion';
import { TILE_STREAM } from './coarseTiles';
import { PROP_LOD } from './propLod';
import { SCENERY_CELLS, TILE_DETAIL } from './sceneryView';
import { canopyClear } from '../render/sceneryMaterial';
import { VISUAL_L0 } from '../core/origin';

/** Props show again this much closer than they hide. */
const PROP_BAND = PROP_LOD.hide - PROP_LOD.show;

export function applyWorldTier(tier: QualityTier) {
  const w = WORLD_TIERS[tier];
  TILE_WINDOW.radius = w.window;
  (TILE_DETAIL as { distance: number }).distance = w.tileDetail;
  // The canopy shells clear the window only, not the whole view-wedge reach: real trees stand only where a full tile is.
  canopyClear.value = Math.max(450, Math.round(w.window * VISUAL_L0 * Math.SQRT2 + 96));
  BLOCK_LOD.hdDistance = w.blockHd;
  BLOCK_LOD.viewScale = w.buildingView;
  Object.assign(PROP_LOD as { hide: number; show: number }, { hide: w.props, show: w.props - PROP_BAND });
  SCENERY_CELLS.distance = w.cells;
  Object.assign(TILE_STREAM, { viewDetail: w.viewDetail, hd3d: w.hd3d, lead: w.lead, detailSlots: w.detailSlots, previewSlots: w.previewSlots,
    returnTiles: w.returnTiles, returnBytes: w.returnBytes });
}
