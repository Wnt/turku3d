import { distToL0, startupDraftTiles } from './coarseRegion';
import { VISUAL_L0 } from '../core/origin';

export interface SceneryView { x: number; y: number; z: number; dx: number; dz: number; halfAngle: number }
export interface ScenerySource { tile: string; key: string; h: [number, number]; source_sha256: string }

/** Live camera footprint, padded by each tile's bounding circle at the frustum edges. */
export function sceneryTiles(radius: number, view: SceneryView) {
  const length = Math.hypot(view.dx, view.dz) || 1, dx = view.dx / length, dz = view.dz / length;
  return startupDraftTiles([view], radius).filter(id => {
    const [, i, j] = id.split('_').map(Number);
    const x = (i + .5) * VISUAL_L0 - view.x, z = (j + .5) * VISUAL_L0 - view.z;
    const distance = Math.hypot(x, z), padding = VISUAL_L0 / Math.SQRT2;
    return distance < padding || Math.acos(Math.max(-1, Math.min(1, (x * dx + z * dz) / distance)))
      <= view.halfAngle + Math.asin(Math.min(1, padding / distance));
  }).sort((a, b) => distToL0(a, view.x, view.z) - distToL0(b, view.x, view.z));
}

/** High angular silhouettes first, so far landmarks don't sit behind hundreds of foreground surfaces. */
export function skylinePriority(source: ScenerySource, view: SceneryView) {
  const [, i, j] = source.tile.split('_').map(Number);
  const x = (i + .5) * VISUAL_L0 - view.x, z = (j + .5) * VISUAL_L0 - view.z;
  const distance = Math.max(1, Math.hypot(x, z));
  const offAxis = Math.acos(Math.max(-1, Math.min(1, (x * view.dx + z * view.dz) / distance)));
  return Math.atan2(source.h[1] - view.y, distance) - offAxis * .02;
}
