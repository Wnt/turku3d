// Road-data agreement for the clients' ambient traffic (game/src/traffic/gate.ts). Every client computes its
// cars from its own copy of each tile's roads.json; they match only when the copies match. A client reports one
// hash per resident tile; each client receives, for the tiles it reported, the hashes that every online player
// who reported that tile shares. A tile with two different reports is agreed by nobody, so it spawns nothing.
export const ROAD_LIMITS = Object.freeze({ tiles: 64 });
const tileOK = id => typeof id === 'string' && /^t0_-?\d{1,4}_-?\d{1,4}$/.test(id);
const hashOK = h => typeof h === 'string' && /^[A-Za-z0-9.:_-]{1,64}$/.test(h);

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

export class RoadAgreement {
  constructor(room) { this.room = room; }

  /** A player's full report of its resident tiles. Returns false when it is malformed. */
  report(p, tiles) {
    if (!tiles || typeof tiles !== 'object' || Array.isArray(tiles)) return false;
    const entries = Object.entries(tiles);
    if (entries.length > ROAD_LIMITS.tiles || !entries.every(([tile, hash]) => tileOK(tile) && hashOK(hash))) return false;
    p.roads = new Map(entries);
    this.flush();
    return true;
  }

  /** Sends each connected player the agreement over its own tiles, when it changed. */
  flush() {
    const agreed = agree([...this.room.players.values()].filter(p => p.online && p.connection && p.roads).map(p => p.roads));
    for (const c of this.room.connections) {
      const p = c.player;
      if (!p?.roads) continue;
      const mine = Object.fromEntries([...p.roads.keys()].sort().flatMap(tile => agreed.has(tile) ? [[tile, agreed.get(tile)]] : []));
      const text = JSON.stringify(mine);
      if (c.roadsSent === text) continue;
      c.roadsSent = text;
      this.room.send(c, 'roads', { agreed: mine });
    }
  }
}
