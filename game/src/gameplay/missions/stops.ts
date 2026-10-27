// Drop-off stops of delivery orders by id: a scripted opening delivery (content/deliveries.ts) or a published pool
// address (content/addresses.ts). A scripted delivery takes its address's drop-off from the pool and keeps its own
// point only as the fallback for a pool that lacks it. A drop-off whose point the ground check rejects
// (missions/dropoffs.ts) moves to its next street-level candidate for the rest of the session.
import type { Router, WorldPoint } from '../contract';
import { addressSlug, type PoolAddress } from '../content/addresses';
import { DELIVERIES, type Delivery } from '../content/deliveries';

/** One drop-off of an order. */
export interface OrderStop {
  id: string;
  address: string;
  point: WorldPoint;
  /** The scripted opening delivery, or null for a pool address. */
  scripted: Delivery | null;
  /** The address in the published pool (a scripted delivery's too, when the pool has it), or null. */
  pool: PoolAddress | null;
}

/** Pool ids of the scripted opening's addresses: the pool offers them only after everything else. */
export const SCRIPTED_IDS: ReadonlySet<string> = new Set(DELIVERIES.map(d => addressSlug(d.address)));
/** Two drop-off candidates closer than this (m) are the same spot. */
const SAME_SPOT_M = 0.5;

export class Stops {
  private readonly byId: ReadonlyMap<string, PoolAddress>;
  /** Drop-offs moved off a rejected point (stop id -> point), and every point rejected per stop. */
  private readonly moved = new Map<string, WorldPoint>();
  private readonly rejected = new Map<string, WorldPoint[]>();

  constructor(readonly pool: readonly PoolAddress[], private readonly router: Router) {
    this.byId = new Map(pool.map(a => [a.id, a]));
  }

  /** A stop by id: `delivery_<n>` or a pool address slug; null when neither exists. */
  resolve(id: string): OrderStop | null {
    const scripted = DELIVERIES.find(d => d.id === id) ?? null;
    const pool = this.byId.get(scripted ? addressSlug(scripted.address) : id) ?? null;
    if (!scripted && !pool) return null;
    const point = this.moved.get(id) ?? pool?.point ?? scripted!.point;
    return { id, address: scripted?.address ?? pool!.address, point, scripted, pool };
  }

  /** Rejects a stop's point and moves it to the first candidate not rejected yet: the pool's drop-off, the scripted
   *  fallback, the pool's street point, then the nearest point of the street network. Null when none is left. */
  relocate(id: string, point: WorldPoint): WorldPoint | null {
    const stop = this.resolve(id);
    if (!stop) return null;
    const rejected = [...this.rejected.get(id) ?? [], point];
    this.rejected.set(id, rejected);
    const street = this.router.route(point, point)?.[0];
    const next = [stop.pool?.point, stop.scripted?.point, stop.pool?.street, street]
      .find(c => c && rejected.every(r => Math.hypot(c.x - r.x, c.z - r.z) > SAME_SPOT_M)) ?? null;
    if (next) this.moved.set(id, next);
    return next;
  }
}
