// Road-data agreement for the clients' ambient traffic (game/src/traffic/gate.ts). Every client computes its
// cars from its own copy of each tile's roads.json; they match only when the copies match. A client reports one
// hash per resident tile; each client receives, for the tiles it reported, the hashes that every online player
// who reported that tile shares. A tile with two different reports is agreed by nobody, so it spawns nothing.
//
// Traffic authority: a tile two or more online players hold (agreed) is run by one of them, its owner, who sends
// the cars there about once a second (`traffic`); the room relays each other holder the tiles it holds, and their
// clients take those cars over (game/src/traffic/flow.ts mirror). Everyone sees the same cars however their
// resident tile sets differ. The owner is kept while it holds the tile; a new one is the holder with the lowest
// id, preferring holders that also hold all eight neighbours (no cars invented at its own edge). An owner whose
// snapshots stop (a stalled tab) is passed over for a while.
export const ROAD_LIMITS = Object.freeze({ tiles: 64,
  /** Traffic snapshots one player may send per second (an owner sends one, in a few pieces when large). */
  trafficPerSecond: 4,
  /** Cars one snapshot message may carry, and the entries one car record may have (flow.ts MirrorCar). */
  trafficCars: 1500, carFields: 11,
  /** An owner with no snapshot for this long (since it took the tile or last sent) is passed over for `muteMs`. */
  staleMs: 6000, muteMs: 60_000,
  /** A player connected this long is preferred as a new owner (its traffic has warmed up by then). */
  settleMs: 20_000 });
const tileOK = id => typeof id === 'string' && /^t0_-?\d{1,4}_-?\d{1,4}$/.test(id);
const hashOK = h => typeof h === 'string' && /^[A-Za-z0-9.:_-]{1,64}$/.test(h);
/** Whether a report holds all eight neighbours of `tile`. */
function surrounded(roads, tile) {
  const [, x, z] = tile.match(/^t0_(-?\d+)_(-?\d+)$/).map(Number);
  for (let dx = -1; dx <= 1; dx++) for (let dz = -1; dz <= 1; dz++) if ((dx || dz) && !roads.has(`t0_${x + dx}_${z + dz}`)) return false;
  return true;
}

/** Per tile, the hash all reports share; tiles with conflicting reports are absent. */
export function agree(reports) {
  const agreed = new Map(), conflict = new Set();
  for (const report of reports) for (const [tile, hash] of report) {
    if (conflict.has(tile)) continue;
    const known = agreed.get(tile);
    if (known === undefined) agreed.set(tile, hash);
    else if (known !== hash) { agreed.delete(tile); conflict.add(tile); }
  }
  return agreed;
}

/** Per tile that two or more of `holders` hold with its agreed hash, the id of the player that runs its traffic.
 *  The previous owner keeps it while it is a candidate, unless it lacks the tile's neighbourhood and a settled
 *  candidate has it. A new owner is the best-ranked candidate (settled before new, then holding all eight
 *  neighbours), the lowest id among equals. `candidate(p)` leaves out muted players (a tile with none has no owner);
 *  `settled(p)`: connected long enough to have warmed its traffic up. */
export function chooseOwners(agreed, holders, previous = new Map(), { candidate = () => true, settled = () => true } = {}) {
  const byTile = new Map();
  for (const p of holders) for (const [tile, hash] of p.roads) {
    if (agreed.get(tile) !== hash) continue;
    const list = byTile.get(tile);
    if (list) list.push(p); else byTile.set(tile, [p]);
  }
  const owners = new Map();
  for (const [tile, list] of byTile) {
    const pool = list.length >= 2 ? list.filter(candidate) : [];
    if (!pool.length) continue;
    const rank = p => (settled(p) ? 0 : 2) + (surrounded(p.roads, tile) ? 0 : 1);
    const best = pool.reduce((a, b) => rank(b) < rank(a) || (rank(b) === rank(a) && b.id < a.id) ? b : a);
    const kept = pool.find(p => p.id === previous.get(tile));
    owners.set(tile, kept && (surrounded(kept.roads, tile) || rank(best) !== 0) ? kept.id : best.id);
  }
  return owners;
}

export class RoadAgreement {
  constructor(room) {
    this.room = room;
    /** tile -> owner id, and tile -> when that owner took it (ms). */
    this.owners = new Map();
    this.since = new Map();
  }

  /** A player's full report of its resident tiles. Returns false when it is malformed. */
  report(p, tiles) {
    if (!tiles || typeof tiles !== 'object' || Array.isArray(tiles)) return false;
    const entries = Object.entries(tiles);
    if (entries.length > ROAD_LIMITS.tiles || !entries.every(([tile, hash]) => tileOK(tile) && hashOK(hash))) return false;
    p.roads = new Map(entries);
    this.flush();
    return true;
  }

  /** Sends each connected player the agreement and the owners over its own tiles, when they changed. */
  flush() {
    const now = this.room.now();
    const holders = [...this.room.players.values()].filter(p => p.online && p.connection && p.roads);
    const agreed = agree(holders.map(p => p.roads));
    const owners = chooseOwners(agreed, holders, this.owners, { candidate: p => !(p.trafficMutedUntil > now),
      settled: p => now - p.connection.joinedAt >= ROAD_LIMITS.settleMs });
    for (const [tile, id] of owners) if (this.owners.get(tile) !== id) this.since.set(tile, now);
    for (const tile of this.since.keys()) if (!owners.has(tile)) this.since.delete(tile);
    this.owners = owners;
    for (const c of this.room.connections) {
      const p = c.player;
      if (!p?.roads) continue;
      const tiles = [...p.roads.keys()].sort();
      const mine = Object.fromEntries(tiles.flatMap(tile => agreed.has(tile) ? [[tile, agreed.get(tile)]] : []));
      const owned = Object.fromEntries(tiles.flatMap(tile => owners.has(tile) ? [[tile, owners.get(tile)]] : []));
      const text = JSON.stringify([mine, owned]);
      if (c.roadsSent === text) continue;
      c.roadsSent = text;
      this.room.send(c, 'roads', { agreed: mine, owners: owned });
    }
  }

  /** An owner's snapshot of the cars on its tiles ({ tick, tiles: { tile: cars } }), relayed to every other online
   *  player holding each tile, as { from, tick, tiles } with only that player's tiles. Tiles the sender does not
   *  own (any more: a hand-over crossing in flight) are left out. Returns false when it is malformed or over budget. */
  traffic(p, m, now) {
    if (!Number.isSafeInteger(m.tick) || m.tick < 0 || !m.tiles || typeof m.tiles !== 'object' || Array.isArray(m.tiles)) return false;
    const entries = Object.entries(m.tiles);
    if (!entries.length || entries.length > ROAD_LIMITS.tiles) return false;
    let cars = 0;
    for (const [tile, list] of entries) {
      if (!tileOK(tile) || !Array.isArray(list) || !list.every(car => Array.isArray(car) && car.length <= ROAD_LIMITS.carFields)) return false;
      cars += list.length;
    }
    if (cars > ROAD_LIMITS.trafficCars) return false;
    p.trafficSent = (p.trafficSent ?? []).filter(t => t > now - 1000);
    if (p.trafficSent.length >= ROAD_LIMITS.trafficPerSecond) return false;
    p.trafficSent.push(now);
    const owned = entries.filter(([tile]) => this.owners.get(tile) === p.id);
    if (!owned.length) return true;
    p.trafficAt = now;
    for (const c of this.room.connections) {
      const q = c.player;
      if (!q || q === p || !q.online || !q.roads) continue;
      const theirs = owned.filter(([tile]) => q.roads.has(tile));
      if (theirs.length) this.room.send(c, 'traffic', { from: p.id, tick: m.tick, tiles: Object.fromEntries(theirs) });
    }
    return true;
  }

  /** Passes over owners whose snapshots stopped, and takes muted players back when their time is up. */
  tick(now) {
    let changed = false;
    for (const [tile, id] of this.owners) {
      const p = this.room.players.get(id);
      if (!p || now - Math.max(this.since.get(tile) ?? now, p.trafficAt ?? 0) <= ROAD_LIMITS.staleMs || p.trafficMutedUntil > now) continue;
      p.trafficMutedUntil = now + ROAD_LIMITS.muteMs;
      changed = true;
    }
    for (const p of this.room.players.values()) {
      if (p.trafficMutedUntil !== undefined && p.trafficMutedUntil <= now) { delete p.trafficMutedUntil; changed = true; }
    }
    if (changed) this.flush();
  }
}
