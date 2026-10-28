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
function parse(id: string): { level: number; i: number; j: number } | null {
  const m = /^t(\d+)_(-?\d+)_(-?\d+)$/.exec(id);
  return m ? { level: +m[1], i: +m[2], j: +m[3] } : null;
}

/** Published 500 m tiles that have no published L0 child: the tileset shows them as leaves. */
export function leafParents(published: Iterable<string>): Set<string> {
  const ids = new Set(published), out = new Set<string>();
  for (const id of ids) {
    const t = parse(id);
    if (t?.level !== 1) continue;
    const children = [0, 1].flatMap(di => [0, 1].map(dj => l0(2 * t.i + di, 2 * t.j + dj)));
    if (!children.some(c => ids.has(c))) out.add(id);
  }
  return out;
}

/** Unpublished L0 ids wanted at (x,z), nearest first: the 3x3 window around the player's cell, widened to all four
 *  quadrants of a published leaf parent so that parent can be swapped out whole. */
export function wantedTiles(x: number, z: number, published: ReadonlySet<string>, leaves: ReadonlySet<string>): string[] {
  const ci = Math.floor(x / VISUAL_L0), cj = Math.floor(z / VISUAL_L0);
  const want = new Set<string>();
  for (let i = ci - 1; i <= ci + 1; i++) for (let j = cj - 1; j <= cj + 1; j++) {
    if (published.has(l0(i, j))) continue;
    want.add(l0(i, j));
    const parent = `t1_${Math.floor(i / 2)}_${Math.floor(j / 2)}`;
    if (!leaves.has(parent)) continue;
    const pi = Math.floor(i / 2), pj = Math.floor(j / 2);
    for (const di of [0, 1]) for (const dj of [0, 1]) want.add(l0(2 * pi + di, 2 * pj + dj));
  }
  return [...want].map(id => ({ id, d: distToL0(id, x, z) })).sort((a, b) => a.d - b.d || (a.id < b.id ? -1 : 1))
    .map(t => t.id);
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
export function aheadTiles(x: number, z: number, vx: number, vz: number, published: ReadonlySet<string>): string[] {
  const speed = Math.hypot(vx, vz);
  if (speed < 1) return [];
  const reach = speed * PREFETCH_SECONDS, steps = Math.ceil(reach / (VISUAL_L0 / 2));
  const want = new Set<string>();
  for (let k = 0; k <= steps; k++) {
    const px = x + vx / speed * reach * k / steps, pz = z + vz / speed * reach * k / steps;
    const ci = Math.floor(px / VISUAL_L0), cj = Math.floor(pz / VISUAL_L0);
    for (let i = ci - 1; i <= ci + 1; i++) for (let j = cj - 1; j <= cj + 1; j++) if (!published.has(l0(i, j))) want.add(l0(i, j));
  }
  return [...want].map(id => ({ id, d: distToL0(id, x, z) })).sort((a, b) => a.d - b.d || (a.id < b.id ? -1 : 1)).map(t => t.id);
}

/** A published leaf parent is hidden while every one of its quadrants is resident as a draft or swapped tile. */
export function parentCovered(parent: string, resident: ReadonlySet<string>): boolean {
  const t = parse(parent);
  if (t?.level !== 1) return false;
  return [0, 1].every(di => [0, 1].every(dj => resident.has(l0(2 * t.i + di, 2 * t.j + dj))));
}

