// Multiplayer gate for ambient traffic (street-richness plan §3.6 "version agreement"). Cars are computed from
// each client's own copy of a tile's roads.json, so they agree only when the copies agree. Every client reports
// one hash per resident tile; the server answers with the hashes all reporting clients share (server/roads.js),
// and a client lets a tile carry cars only when its own hash is the agreed one. Without an answer (an older
// server, a disagreement) a multiplayer tile spawns nothing: a fail-safe, never two different sets of cars.
import type { RoadGraphJson } from '../gameplay/roadGraph';
import { TRAFFIC } from './constants';
import { hex32 } from './hash';

/** Content hash of one tile's road graph, prefixed with the traffic algorithm version. */
export function roadHash(roads: RoadGraphJson): string {
  let h = 0x811c9dc5, g = 0x01000193;
  const mix = (v: number) => {
    const q = Math.round(v * 10) | 0;
    h = Math.imul(h ^ q, 0x01000193); g = Math.imul(g ^ (q >>> 7), 0x5bd1e995);
  };
  const text = (s: string) => { mix(s.length); for (let i = 0; i < s.length; i++) mix(s.charCodeAt(i)); };
  const list = (rows: readonly (readonly number[] | null)[] | undefined) => {
    mix(rows?.length ?? -1);
    for (const row of rows ?? []) { mix(row?.length ?? -1); for (const v of row ?? []) mix(v); }
  };
  mix(roads.version);
  for (const v of roads.nodes) mix(v);
  for (const edge of roads.edges) { mix(edge.length); for (const v of edge) mix(v); }
  list(roads.edgeHeights);
  // Revision 2 fields change lanes, speeds and junctions, so they are part of the agreement too.
  const v2 = roads as RoadGraphJson & { revision?: number; edgeIds?: string[]; edgeAttrColumns?: string[]; edgeAttrs?: number[][];
    nodeFlags?: number[]; edgeEvents?: (number[] | null)[]; controllers?: [string, number, number][] };
  mix(v2.revision ?? 1);
  for (const id of v2.edgeIds ?? []) text(id);
  for (const name of v2.edgeAttrColumns ?? []) text(name);
  list(v2.edgeAttrs); list(v2.nodeFlags ? [v2.nodeFlags] : undefined); list(v2.edgeEvents);
  for (const [id, x, z] of v2.controllers ?? []) { text(id); mix(x); mix(z); }
  return `${TRAFFIC.version}.${roads.nodes.length / 2}.${roads.edges.length}.${hex32(h)}${hex32(g)}`;
}

/** Tiles that may carry cars. Single player (`agreed` null and not shared): every resident tile. Multiplayer:
 *  only tiles whose local hash is the agreed one; nothing before the server's first answer. */
export function enabledTiles(local: ReadonlyMap<string, string>, agreed: ReadonlyMap<string, string> | null, shared: boolean): string[] | null {
  if (!shared) return null;
  if (!agreed) return [];
  return [...local].filter(([tile, hash]) => agreed.get(tile) === hash).map(([tile]) => tile).sort();
}
