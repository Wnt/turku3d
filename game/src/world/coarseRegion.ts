// Pure draft residency and prefetch selection, shared by the service-facing runtime and unit fixtures.
import { COLLISION_TILE, VISUAL_L0 } from '../core/origin';

const PREFETCH_SECONDS = 30;
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
  const reach = speed * PREFETCH_SECONDS, steps = Math.ceil(reach / (VISUAL_L0 / 2));
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
