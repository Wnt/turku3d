import { VISUAL_L0 } from '../core/origin';

export interface SceneryView { x: number; y: number; z: number; dx: number; dz: number; halfAngle: number; focalPixels?: number }
export interface ScenerySource { tile: string; key: string; h: [number, number]; source_sha256: string; trees_url?: string; coverage?: number; regional?: boolean }

/** Ground-plane footprint of a pitched perspective camera. */
export function groundHalfAngle(vertical: number, horizontal: number, fov: number, aspect: number) {
  const tangent = Math.tan(fov * Math.PI / 360);
  return horizontal <= tangent * vertical ? Math.PI
    : Math.atan2(tangent * aspect, horizontal - tangent * vertical) + .15;
}

const boundsCache = new Map<string, Readonly<{x: number; z: number; size: number; level: number}>>();
export function sceneryBounds(id: string) {
  const cached = boundsCache.get(id);
  if (cached) return cached;
  const [level, i, j] = id.slice(1).split('_').map(Number), size = VISUAL_L0 * 2 ** level;
  const bounds = Object.freeze({ x: i * size, z: j * size, size, level });
  // Bound travel-session memory while retaining the complete current city cut.
  if (boundsCache.size >= 4096) boundsCache.clear();
  boundsCache.set(id, bounds);
  return bounds;
}
export function sceneryDistance(id: string, x: number, z: number) {
  const b = sceneryBounds(id);
  return Math.hypot(Math.max(b.x - x, 0, x - b.x - b.size), Math.max(b.z - z, 0, z - b.z - b.size));
}
export function sceneryContains(parent: string, child: string) {
  const a = sceneryBounds(parent), b = sceneryBounds(child);
  return a.level >= b.level && b.x >= a.x && b.z >= a.z && b.x + b.size <= a.x + a.size && b.z + b.size <= a.z + a.size;
}

/** Project a 1.5 m facade feature into drawing-buffer pixels. Survey kilometre
 * nodes retain roof silhouettes; authored facade detail refines only while it
 * occupies two pixels. The near reserve covers the playable neighbourhood. */
export function sceneryDetailDistance(view: SceneryView) {
  return Math.max(350, Math.min(2000, (view.focalPixels ?? 600) * 1.5 / 2));
}

/** Camera distance (3D, m, with hysteresis) beyond which a resident full tile's visual yields to its scenery cell.
 *  Every tile of the 3x3 window is within 354 m of a camera on the ground, so this only acts on a high camera. */
export const TILE_DETAIL = { distance: 450, band: 40 } as const;
/** L0 refinement always reaches past any full tile still drawing (horizontal distance never exceeds the 3D one;
 *  the slack covers rounding at the band edge), so a 1 km node never draws over HD squares. */
const DETAIL_FLOOR = TILE_DETAIL.distance + TILE_DETAIL.band + 90;

/** Non-overlapping quadtree cut: authored shells nearby, source-built kilometre groups farther out. */
export function sceneryTiles(radius: number, view: SceneryView, previous: readonly string[] = []) {
  const length = Math.hypot(view.dx, view.dz) || 1, dx = view.dx / length, dz = view.dz / length;
  const result: string[] = [];
  const detailDistance = sceneryDetailDistance(view);
  const detailedParents = new Set(previous.filter(id => sceneryBounds(id).level === 0).map(id => {
    const b = sceneryBounds(id);
    return `t2_${Math.floor(b.x / 1000)}_${Math.floor(b.z / 1000)}`;
  }));
  const visit = (level: number, i: number, j: number) => {
    const id = `t${level}_${i}_${j}`, size = VISUAL_L0 * 2 ** level;
    const distance = sceneryDistance(id, view.x, view.z);
    if (distance > radius) return;
    const x = (i + .5) * size - view.x, z = (j + .5) * size - view.z;
    const centre = Math.hypot(x, z), padding = size / Math.SQRT2;
    const inView = centre <= padding || Math.acos(Math.max(-1, Math.min(1, (x * dx + z * dz) / centre)))
      <= view.halfAngle + Math.asin(Math.min(1, padding / centre));
    // Coarse city geometry remains around the camera during rapid turns. Refine only
    // the viewed middle distance; the old cut remains until all replacements load.
    // A 20% hysteresis band avoids switching whole city blocks back and forth
    // as adaptive resolution or a moving camera crosses the pixel threshold.
    const detailLimit = Math.max(DETAIL_FLOOR, detailDistance * (detailedParents.has(id) ? 1.2 : 1));
    // Refinement stays horizontal: a 1 km node has no streets and raw survey shells, far poorer than the 250 m
    // cells, so a high camera keeps the cells under it (height-aware detail is the full-tile -> cell swap instead).
    const refine = level === 4 ? distance < 4500 : level === 2 ? distance < detailLimit && inView : false;
    if (!refine) { result.push(id); return; }
    const childLevel = level - 2;
    for (let a = 0; a < 4; a++) for (let b = 0; b < 4; b++) visit(childLevel, i * 4 + a, j * 4 + b);
  };
  const size = 4000;
  for (let i = Math.floor((view.x - radius) / size); i <= Math.floor((view.x + radius) / size); i++)
    for (let j = Math.floor((view.z - radius) / size); j <= Math.floor((view.z + radius) / size); j++) visit(4, i, j);
  return result.sort((a, b) => sceneryDistance(a, view.x, view.z) - sceneryDistance(b, view.x, view.z));
}

/** High angular silhouettes first, so far landmarks do not wait behind foreground terrain. */
export function skylinePriority(source: ScenerySource, view: SceneryView) {
  const b = sceneryBounds(source.tile);
  const x = b.x + b.size / 2 - view.x, z = b.z + b.size / 2 - view.z;
  const distance = Math.max(1, Math.hypot(x, z));
  const direction = Math.max(1e-9, Math.hypot(view.dx, view.dz));
  const offAxis = Math.acos(Math.max(-1, Math.min(1, (x * view.dx + z * view.dz) / (distance * direction))));
  return Math.atan2(source.h[1] - view.y, distance) - offAxis * .02;
}


/** A camera-following, all-direction terrain reserve survives fast turns and city tile loading. */
export function regionalTiles(view: {x: number; z: number}, radius = 15000) {
  const result: string[] = [], size = 4000;
  for (let i = Math.floor((view.x-radius)/size); i <= Math.floor((view.x+radius)/size); i++)
    for (let j = Math.floor((view.z-radius)/size); j <= Math.floor((view.z+radius)/size); j++) {
      const id = `t4_${i}_${j}`;
      if (sceneryDistance(id,view.x,view.z) <= radius) result.push(id);
    }
  return result.sort((a,b)=>sceneryDistance(a,view.x,view.z)-sceneryDistance(b,view.x,view.z));
}
