import { VISUAL_L0 } from '../core/origin';

export interface SceneryView { x: number; y: number; z: number; dx: number; dz: number; halfAngle: number }
export interface ScenerySource { tile: string; key: string; h: [number, number]; source_sha256: string; trees_url?: string; coverage?: number; regional?: boolean }

/** Ground-plane footprint of a pitched perspective camera. */
export function groundHalfAngle(vertical: number, horizontal: number, fov: number, aspect: number) {
  const tangent = Math.tan(fov * Math.PI / 360);
  return horizontal <= tangent * vertical ? Math.PI
    : Math.atan2(tangent * aspect, horizontal - tangent * vertical) + .15;
}

export function sceneryBounds(id: string) {
  const [level, i, j] = id.slice(1).split('_').map(Number), size = VISUAL_L0 * 2 ** level;
  return { x: i * size, z: j * size, size, level };
}
export function sceneryDistance(id: string, x: number, z: number) {
  const b = sceneryBounds(id);
  return Math.hypot(Math.max(b.x - x, 0, x - b.x - b.size), Math.max(b.z - z, 0, z - b.z - b.size));
}
export function sceneryContains(parent: string, child: string) {
  const a = sceneryBounds(parent), b = sceneryBounds(child);
  return a.level >= b.level && b.x >= a.x && b.z >= a.z && b.x + b.size <= a.x + a.size && b.z + b.size <= a.z + a.size;
}

/** Non-overlapping quadtree cut: authored shells nearby, source-built kilometre groups farther out. */
export function sceneryTiles(radius: number, view: SceneryView) {
  const length = Math.hypot(view.dx, view.dz) || 1, dx = view.dx / length, dz = view.dz / length;
  const result: string[] = [];
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
    const refine = level === 4 ? distance < 4500 : level === 2 ? distance < 2000 && inView : false;
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
