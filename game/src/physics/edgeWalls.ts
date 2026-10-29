// Invisible walls along the true edges of the collision: wherever a loaded .col tile borders a cell that has no
// collision in any source (outside the published tiles and every draft, a draft still building or rejected, a tile
// whose load failed for good), a tall box closes that edge. The character and every vehicle stop there instead of
// walking or driving onto visible ground (a far LOD) that has nothing to stand on. A neighbour whose collision exists
// but is not loaded yet (`known`, sent by world/collisionStreaming.ts) gets no wall: the streamer loads it ahead of
// the player and holds the player's speed near it instead. Until the main thread names a cell known, an edge towards
// it is closed. Scene queries (rays, the camera cast) pass through: the walls are members of STATIC_SOLID but filter
// only the bodies that move. The open edges also feed the visible edge marker (`edges()`).
import type * as RAPIER_NS from '@dimforge/rapier3d';
import { COLLISION_TILE } from '../core/origin';
import { GROUP } from '../shared/col/materials';

type R = typeof RAPIER_NS;

const EDGE_WALL = {
  /** Half thickness (m); the wall lies wholly outside the loaded tile. A car at 40 m/s moves 0.67 m per 60 Hz step. */
  half: 1,
  /** Reach below the tile's lowest and above its highest collision vertex (m). */
  below: 20, above: 40,
} as const;

/** Neighbour offsets: +x, -x, +z, -z. */
const SIDES = [[1, 0], [-1, 0], [0, 1], [0, -1]] as const;

interface Cell { i: number; j: number; lo: number; hi: number }

export class EdgeWalls {
  private cells = new Map<string, Cell>();
  private walls = new Map<string, RAPIER_NS.Collider>();
  private wallHandles = new Set<number>();
  /** Cells whose collision exists somewhere (published or draft), loaded or not. */
  private known = new Set<string>();

  constructor(private readonly R: R, private readonly world: RAPIER_NS.World) {}

  /** A collision tile at grid cell (i, j), its vertices spanning heights lo..hi, is now in the world. */
  add(i: number, j: number, lo: number, hi: number) {
    this.cells.set(key(i, j), { i, j, lo, hi });
    this.sync(i, j);
    for (const [di, dj] of SIDES) this.sync(i + di, j + dj);
  }

  /** The collision tile at (i, j) left the world. */
  remove(i: number, j: number) {
    if (!this.cells.delete(key(i, j))) return;
    this.sync(i, j);
    for (const [di, dj] of SIDES) this.sync(i + di, j + dj);
  }

  /** Cells (`c_i_j`) whose collision exists but may not be loaded join (`add`) or leave (`remove`) the known set. */
  setKnown(add: Iterable<string>, remove: Iterable<string>) {
    const touched: [number, number][] = [];
    for (const [ids, on] of [[add, true], [remove, false]] as const) for (const id of ids) {
      const m = /^c_(-?\d+)_(-?\d+)$/.exec(id);
      if (!m) continue;
      const k = `${m[1]}_${m[2]}`;
      if (on === this.known.has(k)) continue;
      if (on) this.known.add(k); else this.known.delete(k);
      touched.push([+m[1], +m[2]]);
    }
    for (const [i, j] of touched) for (const [di, dj] of SIDES) this.sync(i + di, j + dj);
  }

  /** Flight ignores only these artificial barriers; real world colliders still block it. */
  isWall(collider: RAPIER_NS.Collider): boolean { return this.wallHandles.has(collider.handle); }

  /** Open edges as [i, j, side] with side an index into +x, -x, +z, -z (tests, stats and the edge marker). */
  edges(): [number, number, number][] {
    return [...this.walls.keys()].map(k => k.split('_').map(Number) as [number, number, number]).sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2]);
  }

  private sync(i: number, j: number) {
    const cell = this.cells.get(key(i, j));
    SIDES.forEach(([di, dj], side) => {
      const id = `${key(i, j)}_${side}`;
      const n = key(i + di, j + dj);
      const open = cell !== undefined && !this.cells.has(n) && !this.known.has(n);
      const wall = this.walls.get(id);
      if (open && !wall) {
        const built = this.build(cell, side);
        this.walls.set(id, built); this.wallHandles.add(built.handle);
      } else if (!open && wall) {
        this.wallHandles.delete(wall.handle); this.world.removeCollider(wall, false); this.walls.delete(id);
      }
    });
  }

  private build(c: Cell, side: number): RAPIER_NS.Collider {
    const { half, below, above } = EDGE_WALL;
    const [di, dj] = SIDES[side];
    const y0 = c.lo - below, y1 = c.hi + above;
    const cx = (c.i + 0.5) * COLLISION_TILE, cz = (c.j + 0.5) * COLLISION_TILE;
    const out = COLLISION_TILE / 2 + half;
    // Along the edge the wall overhangs both corners by its thickness, so two walls meeting at a corner close it.
    const along = COLLISION_TILE / 2 + 2 * half;
    const desc = this.R.ColliderDesc.cuboid(di ? half : along, (y1 - y0) / 2, dj ? half : along)
      .setTranslation(cx + di * out, (y0 + y1) / 2, cz + dj * out)
      .setFriction(0)
      .setCollisionGroups(((GROUP.STATIC_SOLID & 0xffff) << 16) | (GROUP.CHARACTER | GROUP.VEHICLE | GROUP.DYNAMIC));
    return this.world.createCollider(desc);
  }
}

const key = (i: number, j: number) => `${i}_${j}`;
