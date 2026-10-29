// Pure draft residency and prefetch selection, shared by the service-facing runtime and unit fixtures.
import { COLLISION_TILE, VISUAL_L0 } from '../core/origin';

const PREFETCH_SECONDS = 60;
const MAX_AHEAD_METRES = 12 * VISUAL_L0;
const l0 = (i: number, j: number) => `t0_${i}_${j}`;
/** Distance (m) from (x,z) to the footprint of L0 tile `id`. */
export function distToL0(id: string, x: number, z: number): number {
  const t = parse(id);
  if (!t) return Infinity;
  const x0 = t.i * VISUAL_L0, z0 = t.j * VISUAL_L0;
  return Math.hypot(Math.max(x0 - x, 0, x - (x0 + VISUAL_L0)), Math.max(z0 - z, 0, z - (z0 + VISUAL_L0)));
}
/** Entry time (s) along the player's velocity ray; Infinity for cells off the path. */
export function arrivalToL0(id: string, x: number, z: number, vx: number, vz: number): number {
  const tile = parse(id);
  if (!tile || Math.hypot(vx, vz) < 1) return Infinity;
  let enter = 0, leave = Infinity;
  for (const [position, velocity, lo] of [[x, vx, tile.i * VISUAL_L0], [z, vz, tile.j * VISUAL_L0]]) {
    if (Math.abs(velocity) < 1e-9) {
      if (position < lo || position >= lo + VISUAL_L0) return Infinity;
    } else {
      const a = (lo - position) / velocity, b = (lo + VISUAL_L0 - position) / velocity;
      enter = Math.max(enter, Math.min(a, b)); leave = Math.min(leave, Math.max(a, b));
    }
  }
  return leave > enter ? enter : Infinity;
}

function parse(id: string): { level: number; i: number; j: number } | null {
  const m = /^t(\d+)_(-?\d+)_(-?\d+)$/.exec(id);
  return m ? { level: +m[1], i: +m[2], j: +m[3] } : null;
}

/** Nearest-first 3x3 window around the player, including the former starting area. */
export function wantedTiles(x: number, z: number): string[] {
  const ci = Math.floor(x / VISUAL_L0), cj = Math.floor(z / VISUAL_L0);
  return Array.from({ length: 9 }, (_, k) => l0(ci - 1 + k % 3, cj - 1 + Math.floor(k / 3)))
    .sort((a, b) => distToL0(a, x, z) - distToL0(b, x, z) || a.localeCompare(b));
}

/** Retain the normal window plus 75 m of boundary hysteresis, at most 4x4 per start. */
export function retainDraftTile(id: string, points: readonly { x: number; z: number }[]) {
  const tile = parse(id);
  if (!tile || tile.level !== 0) return false;
  const x0 = tile.i * VISUAL_L0, z0 = tile.j * VISUAL_L0;
  return points.some(point => Math.max(x0 - point.x, point.x - x0 - VISUAL_L0,
    z0 - point.z, point.z - z0 - VISUAL_L0) <= VISUAL_L0 + 75);
}

/** The 16 collision cells (62.5 m) of L0 tile `id`. */
export function cellsOf(id: string): string[] {
  const t = parse(id);
  if (!t || t.level !== 0) return [];
  const per = VISUAL_L0 / COLLISION_TILE;
  return Array.from({ length: per * per }, (_, k) => `c_${t.i * per + (k % per)}_${t.j * per + Math.floor(k / per)}`);
}

/** Unpublished L0 ids to build ahead of a player moving at (vx, vz) m/s: every cell within the next
 *  PREFETCH_SECONDS of travel along the heading plus a one-tile ring, nearest first. Empty below walking speed. */
export function aheadTiles(x: number, z: number, vx: number, vz: number): string[] {
  const speed = Math.hypot(vx, vz);
  if (speed < 1) return [];
  const reach = Math.min(speed * PREFETCH_SECONDS, MAX_AHEAD_METRES), steps = Math.ceil(reach / (VISUAL_L0 / 2));
  const want = new Set<string>();
  for (let k = 0; k <= steps; k++) {
    const px = x + vx / speed * reach * k / steps, pz = z + vz / speed * reach * k / steps;
    const ci = Math.floor(px / VISUAL_L0), cj = Math.floor(pz / VISUAL_L0);
    for (let i = ci - 1; i <= ci + 1; i++) for (let j = cj - 1; j <= cj + 1; j++) want.add(l0(i, j));
  }
  return [...want].map(id => ({ id, d: distToL0(id, x, z) })).sort((a, b) => a.d - b.d || (a.id < b.id ? -1 : 1)).map(t => t.id);
}

/** Startup needs collision around each selectable start, rather than waiting for whole far-LOD sibling groups. */
export function startupDraftTiles(points: readonly { x: number; z: number }[], radius: number): string[] {
  const wanted = new Set<string>();
  for (const { x, z } of points) {
    for (let i = Math.floor((x - radius) / VISUAL_L0); i <= Math.floor((x + radius) / VISUAL_L0); i++) {
      for (let j = Math.floor((z - radius) / VISUAL_L0); j <= Math.floor((z + radius) / VISUAL_L0); j++) {
        const id = l0(i, j);
        if (distToL0(id, x, z) <= radius) wanted.add(id);
      }
    }
  }
  return [...wanted];
}

/** Anonymous demand order: swept crossings, visible neighbours, then the background ring. */
export function draftDemand(visible: string[], ahead: string[], x: number, z: number, vx: number, vz: number, path?: readonly string[]) {
  const here = `t0_${Math.floor(x / VISUAL_L0)}_${Math.floor(z / VISUAL_L0)}`;
  const ordered = [...new Set([...visible, ...ahead])].sort((a, b) => {
    if (a === here || b === here) return a === here ? -1 : 1;
    if (path) {
      const aa = path.indexOf(a), bb = path.indexOf(b);
      if (aa >= 0 || bb >= 0) return (aa < 0 ? Infinity : aa) - (bb < 0 ? Infinity : bb);
    }
    const aa = arrivalToL0(a, x, z, vx, vz), bb = arrivalToL0(b, x, z, vx, vz);
    return aa - bb || Number(visible.includes(b)) - Number(visible.includes(a))
      || distToL0(a, x, z) - distToL0(b, x, z) || a.localeCompare(b);
  }).slice(0, 128);
  const urgent = ordered.filter(id => id === here || (path ? path.includes(id) : Number.isFinite(arrivalToL0(id, x, z, vx, vz))));
  return { tiles: ordered, urgent, visible: ordered.filter(id => visible.includes(id)) };
}

/** Prepare one imminent path tile beyond the normal window, never the whole build-ahead cone. */
export function lookaheadTile(visible: string[], ahead: string[], x: number, z: number, vx: number, vz: number, path?: readonly string[]) {
  const speed = Math.hypot(vx, vz);
  if (speed < 1) return undefined;
  return draftDemand(visible, ahead, x, z, vx, vz, path).urgent.find(id => {
    if (visible.includes(id)) return false;
    const distance = distToL0(id, x, z);
    const arrival = path ? distance / speed : arrivalToL0(id, x, z, vx, vz);
    return distance <= 2 * VISUAL_L0 && arrival <= 30;
  });
}

/** Remaining route corridor within 60 s, capped at 3 km; ignores stale/off-route hints and never sends the polyline. */
export function routeAheadTiles(points: readonly { x: number; z: number }[], x: number, z: number, speed: number) {
  if (speed < 1 || points.length < 2) return null;
  let nearest = Infinity, index = 0, fraction = 0;
  for (let k = 0; k + 1 < points.length; k++) {
    const a = points[k], b = points[k + 1], dx = b.x - a.x, dz = b.z - a.z;
    const t = Math.max(0, Math.min(1, ((x - a.x) * dx + (z - a.z) * dz) / (dx * dx + dz * dz || 1)));
    const distance = Math.hypot(a.x + t * dx - x, a.z + t * dz - z);
    if (distance < nearest) { nearest = distance; index = k; fraction = t; }
  }
  if (nearest > 80) return null;
  let left = Math.min(speed * PREFETCH_SECONDS, MAX_AHEAD_METRES);
  let a = { x: points[index].x + fraction * (points[index + 1].x - points[index].x),
    z: points[index].z + fraction * (points[index + 1].z - points[index].z) };
  const path = new Set<string>();
  for (let k = index + 1; k < points.length && left > 0; k++) {
    const end = points[k], length = Math.hypot(end.x - a.x, end.z - a.z);
    const portion = Math.min(1, left / (length || 1));
    const b = { x: a.x + portion * (end.x - a.x), z: a.z + portion * (end.z - a.z) };
    const cuts = [0, 1];
    for (const axis of ['x', 'z'] as const) {
      const delta = b[axis] - a[axis];
      if (Math.abs(delta) < 1e-9) continue;
      for (let edge = (Math.floor(Math.min(a[axis], b[axis]) / VISUAL_L0) + 1) * VISUAL_L0;
        edge < Math.max(a[axis], b[axis]); edge += VISUAL_L0) cuts.push((edge - a[axis]) / delta);
    }
    cuts.sort((aa, bb) => aa - bb);
    for (let c = 0; c + 1 < cuts.length; c++) {
      const t = (cuts[c] + cuts[c + 1]) / 2;
      path.add(l0(Math.floor((a.x + t * (b.x - a.x)) / VISUAL_L0), Math.floor((a.z + t * (b.z - a.z)) / VISUAL_L0)));
    }
    left -= length * portion; a = b;
  }
  const tiles = new Set<string>();
  for (const id of path) {
    const tile = parse(id)!;
    for (let i = tile.i - 1; i <= tile.i + 1; i++) for (let j = tile.j - 1; j <= tile.j + 1; j++) tiles.add(l0(i, j));
  }
  return path.size ? { tiles: [...tiles], urgent: [...path] } : null;
}
