// Claimed traffic slots. A claim key is the car's identity in its tile (TrafficNetwork.key): local claims come from
// this client's physics, remote ones from the vehicle registry's sources (`tr:<key>`). A car keeps its key while
// it stays in the tile; where a claimed slot's timetable crosses a seam it has a new key in the next tile, which
// every client holding both tiles derives the same way, so the car stays gone there too.
import { TRAFFIC } from './constants';
import type { TrafficNetwork } from './network';
import type { Lane } from './tileNetwork';

export class ClaimSet {
  private local = new Set<string>();
  private remote = new Set<string>();
  private cursors = new Map<string, { lane: Lane; n: number } | null>();
  private derived = new Set<string>();

  constructor(private readonly network: TrafficNetwork) {}

  get size() { return new Set([...this.local, ...this.remote]).size; }
  addLocal(key: string) { this.local.add(key); }
  setRemote(keys: Iterable<string>) { this.remote = new Set(keys); this.cursors.clear(); }
  /** Lanes changed: every cursor starts again from its claim. */
  reset() { this.cursors.clear(); this.derived.clear(); }

  has(key: string) { return this.local.has(key) || this.remote.has(key) || this.derived.has(key); }

  /** Moves every claim's cursor to time t and collects the keys its slot has in tiles it has entered since. */
  follow(t: number) {
    this.derived.clear();
    for (const key of new Set([...this.local, ...this.remote])) {
      let cursor = this.cursors.get(key);
      if (cursor === undefined) this.cursors.set(key, cursor = this.start(key));
      if (!cursor) continue;
      for (let guard = 0; guard < 256; guard++) {
        const next = this.network.continuation(cursor.lane, cursor.n);
        if (!next || next.lane.phase + next.n * TRAFFIC.headway > t) break;
        cursor = next;
        this.derived.add(this.network.key(cursor.lane, cursor.n));
      }
      this.cursors.set(key, cursor);
    }
  }

  /** The origin lane and slot of a key whose tile is resident; null otherwise. A car on a closed chain inside one
   *  tile never leaves it, so its key alone removes it. */
  private start(key: string): { lane: Lane; n: number } | null {
    const [tile, origin, n0] = key.split(':');
    const lane = this.network.tiles.get(tile)?.byId.get(origin);
    const n = Number(n0);
    if (!lane || !Number.isInteger(n) || lane.cycle) return null;
    return { lane, n };
  }
}
