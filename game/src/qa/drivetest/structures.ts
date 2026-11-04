// Every bridge, tunnel and underpass a car can drive, and the lanes on each, for the drive sweep.
//
// Sources: each tile's roads.json (car edges with the v2 BRIDGE / TUNNEL flags and surface heights; rows are clipped
// at tile seams and grouped back by OSM way) and the city's OSM bridge and tunnel ways (names, and rail bridges,
// which roads.json lacks). An underpass is a car edge that crosses, in plan, a bridge of any kind (road, path, rail)
// whose deck is at least UNDER_CLEARANCE above it (or, for an OSM-only rail bridge, any car edge under it that is not
// itself a bridge). Lanes are the autopilot router's (gameplay/autopilot/lanes.ts), matched by geometry.
import { CAR, EDGE, parseRoadGraph, type RoadGraphJson } from '../../gameplay/roadGraph';
import type { Lane } from '../../traffic/tileNetwork';
import { engineToEN } from '../../core/origin';

export interface OsmWay { way: number; kind: 'bridge' | 'tunnel'; mode: 'road' | 'rail' | 'path' | 'other'; name: string; tags: Record<string, string>; points: [number, number][] }
export interface Polyline { x: number[]; z: number[]; y: (number | null)[] }

export interface Structure {
  id: string;
  kind: 'bridge' | 'tunnel' | 'underpass';
  name: string;
  /** OSM way of the drivable road (the deck, the tunnel or the road below). */
  way: number;
  highway: string;
  /** For an underpass: what passes over it. */
  over?: { way: number; name: string; mode: string };
  tiles: string[];
  /** The drivable stretch: roads.json rows (a bridge or tunnel), or ±UNDER_REACH around the crossing (underpass). */
  lines: Polyline[];
  width: number;
  length: number;
  center: { x: number; z: number; E: number; N: number };
}

const UNDER_CLEARANCE = 2.5;
const UNDER_REACH = 25;

interface Row { tile: string; way: number; id: string; highway: string; flags: number; car: boolean; width: number; xs: number[]; zs: number[]; ys: (number | null)[] }

function rowsOf(tiles: readonly { tile: string; json: RoadGraphJson }[]): Row[] {
  const rows: Row[] = [];
  for (const { tile, json } of tiles) {
    const g = parseRoadGraph(json);
    for (const e of g.edges) {
      const n = e.points.length / 2;
      rows.push({ tile, way: e.way, id: e.id, highway: e.highway, flags: e.eflags, car: !!(e.flags & CAR), width: e.lanes.width,
        xs: Array.from({ length: n }, (_, i) => e.points[2 * i]), zs: Array.from({ length: n }, (_, i) => e.points[2 * i + 1]),
        ys: Array.from({ length: n }, (_, i) => e.heights?.[i] ?? null) });
    }
  }
  return rows;
}

const lengthOf = (l: Polyline) => { let s = 0; for (let i = 1; i < l.x.length; i++) s += Math.hypot(l.x[i] - l.x[i - 1], l.z[i] - l.z[i - 1]); return s; };

function centre(lines: Polyline[]) {
  let x = 0, z = 0, n = 0;
  for (const l of lines) for (let i = 0; i < l.x.length; i++) { x += l.x[i]; z += l.z[i]; n++; }
  x /= n || 1; z /= n || 1;
  const en = engineToEN(x, 0, z);
  return { x, z, E: Math.round(en.E * 10) / 10, N: Math.round(en.N * 10) / 10 };
}

/** Segment intersection in plan: parameters along both segments, or null. */
function cross(ax: number, az: number, bx: number, bz: number, cx: number, cz: number, dx: number, dz: number) {
  const rx = bx - ax, rz = bz - az, sx = dx - cx, sz = dz - cz, den = rx * sz - rz * sx;
  if (Math.abs(den) < 1e-9) return null;
  const t = ((cx - ax) * sz - (cz - az) * sx) / den, u = ((cx - ax) * rz - (cz - az) * rx) / den;
  return t >= 0 && t <= 1 && u >= 0 && u <= 1 ? { t, u } : null;
}

export function findStructures(tiles: readonly { tile: string; json: RoadGraphJson }[], osm: readonly OsmWay[] = []): Structure[] {
  const rows = rowsOf(tiles);
  const named = new Map(osm.map(w => [w.way, w]));
  const out: Structure[] = [];
  // Bridges and tunnels: car rows by OSM way (a way's rows over several tiles are one structure).
  for (const [flag, kind] of [[EDGE.BRIDGE, 'bridge'], [EDGE.TUNNEL, 'tunnel']] as const) {
    const groups = new Map<string, Row[]>();
    for (const r of rows) if (r.car && r.flags & flag) {
      const key = r.way ? `w${r.way}` : `e${r.id}`;
      const list = groups.get(key); if (list) list.push(r); else groups.set(key, [r]);
    }
    for (const [key, list] of groups) {
      const lines = list.map(r => ({ x: r.xs, z: r.zs, y: r.ys }));
      const way = list[0].way;
      out.push({ id: `${kind}:${key}`, kind, name: named.get(way)?.name ?? '', way, highway: list[0].highway,
        tiles: [...new Set(list.map(r => r.tile))].sort(), lines, width: Math.max(...list.map(r => r.width)),
        length: lines.reduce((a, l) => a + lengthOf(l), 0), center: centre(lines) });
    }
  }
  // Underpasses: car rows (not decks, not tunnels) crossing below a bridge of any mode.
  const decks: { way: number; name: string; mode: string; xs: number[]; zs: number[]; ys: (number | null)[] }[] = [
    ...rows.filter(r => r.flags & EDGE.BRIDGE).map(r => ({ way: r.way, name: named.get(r.way)?.name ?? '', mode: r.car ? 'road' : 'path', xs: r.xs, zs: r.zs, ys: r.ys })),
    // OSM bridges roads.json does not carry (rail, cable car): no deck height, any car row crossing is below.
    ...osm.filter(w => w.kind === 'bridge' && (w.mode === 'rail' || w.mode === 'other'))
      .map(w => ({ way: w.way, name: w.name, mode: w.mode, xs: w.points.map(p => p[0]), zs: w.points.map(p => p[1]), ys: w.points.map(() => null) })),
  ];
  const grid = new Map<string, number[]>();
  const CELL = 50;
  decks.forEach((d, k) => {
    const seen = new Set<string>();
    for (let i = 0; i < d.xs.length; i++) {
      const key = `${Math.floor(d.xs[i] / CELL)},${Math.floor(d.zs[i] / CELL)}`;
      if (!seen.has(key)) { seen.add(key); (grid.get(key) ?? grid.set(key, []).get(key)!).push(k); }
    }
    // Long straight segments: their midpoints too.
    for (let i = 1; i < d.xs.length; i++) {
      const steps = Math.ceil(Math.hypot(d.xs[i] - d.xs[i - 1], d.zs[i] - d.zs[i - 1]) / CELL);
      for (let s = 1; s < steps; s++) {
        const x = d.xs[i - 1] + (d.xs[i] - d.xs[i - 1]) * s / steps, z = d.zs[i - 1] + (d.zs[i] - d.zs[i - 1]) * s / steps;
        const key = `${Math.floor(x / CELL)},${Math.floor(z / CELL)}`;
        if (!seen.has(key)) { seen.add(key); (grid.get(key) ?? grid.set(key, []).get(key)!).push(k); }
      }
    }
  });
  const under = new Map<string, Structure>();
  for (const r of rows) {
    if (!r.car || r.flags & (EDGE.BRIDGE | EDGE.TUNNEL)) continue;
    for (let i = 1; i < r.xs.length; i++) {
      const candidates = new Set<number>();
      for (const [x, z] of [[r.xs[i - 1], r.zs[i - 1]], [r.xs[i], r.zs[i]], [(r.xs[i - 1] + r.xs[i]) / 2, (r.zs[i - 1] + r.zs[i]) / 2]]) {
        const cx = Math.floor(x / CELL), cz = Math.floor(z / CELL);
        for (let a = -1; a <= 1; a++) for (let b = -1; b <= 1; b++) for (const k of grid.get(`${cx + a},${cz + b}`) ?? []) candidates.add(k);
      }
      for (const k of candidates) {
        const d = decks[k];
        if (d.way && d.way === r.way) continue;
        for (let j = 1; j < d.xs.length; j++) {
          const hit = cross(r.xs[i - 1], r.zs[i - 1], r.xs[i], r.zs[i], d.xs[j - 1], d.zs[j - 1], d.xs[j], d.zs[j]);
          if (!hit) continue;
          const roadY = r.ys[i - 1] !== null && r.ys[i] !== null ? r.ys[i - 1]! + (r.ys[i]! - r.ys[i - 1]!) * hit.t : null;
          const deckY = d.ys[j - 1] !== null && d.ys[j] !== null ? d.ys[j - 1]! + (d.ys[j]! - d.ys[j - 1]!) * hit.u : null;
          if (roadY !== null && deckY !== null && deckY - roadY < UNDER_CLEARANCE) continue;
          const x = r.xs[i - 1] + (r.xs[i] - r.xs[i - 1]) * hit.t, z = r.zs[i - 1] + (r.zs[i] - r.zs[i - 1]) * hit.t;
          const key = `under:w${r.way || r.id}>w${d.way}`;
          const line = clipAround(r, i, hit.t, UNDER_REACH);
          const existing = under.get(key);
          if (existing) {
            // One structure per road way under one bridge way; its rows in other tiles add their stretch.
            if (!existing.tiles.includes(r.tile)) existing.tiles.push(r.tile);
            if (existing.lines.every(l => Math.hypot(l.x[0] - line.x[0], l.z[0] - line.z[0]) > 5)) existing.lines.push(line);
            continue;
          }
          under.set(key, { id: key, kind: 'underpass', name: named.get(r.way)?.name ?? '', way: r.way, highway: r.highway,
            over: { way: d.way, name: d.name, mode: d.mode }, tiles: [r.tile], lines: [line], width: r.width, length: 2 * UNDER_REACH,
            center: centre([{ x: [x], z: [z], y: [roadY] }]) });
        }
      }
    }
  }
  out.push(...under.values());
  return out.sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
}

/** The row's polyline within `reach` metres of the point at segment i, parameter t. */
function clipAround(r: Row, i: number, t: number, reach: number): Polyline {
  const cum = [0];
  for (let k = 1; k < r.xs.length; k++) cum.push(cum[k - 1] + Math.hypot(r.xs[k] - r.xs[k - 1], r.zs[k] - r.zs[k - 1]));
  const at = cum[i - 1] + (cum[i] - cum[i - 1]) * t, s0 = at - reach, s1 = at + reach;
  const line: Polyline = { x: [], z: [], y: [] };
  const put = (s: number) => {
    let k = 1; while (k < cum.length - 1 && cum[k] < s) k++;
    const f = Math.max(0, Math.min(1, (s - cum[k - 1]) / ((cum[k] - cum[k - 1]) || 1)));
    line.x.push(r.xs[k - 1] + (r.xs[k] - r.xs[k - 1]) * f); line.z.push(r.zs[k - 1] + (r.zs[k] - r.zs[k - 1]) * f);
    const y0 = r.ys[k - 1], y1 = r.ys[k];
    line.y.push(y0 !== null && y1 !== null ? y0 + (y1 - y0) * f : null);
  };
  put(Math.max(0, s0));
  for (let k = 0; k < cum.length; k++) if (cum[k] > s0 && cum[k] < s1) { line.x.push(r.xs[k]); line.z.push(r.zs[k]); line.y.push(r.ys[k]); }
  put(Math.min(cum.at(-1)!, s1));
  return line;
}

/** Distance in plan from a point to a polyline, with the line's height there. */
function nearLine(l: Polyline, x: number, z: number): { d: number; y: number | null } {
  let best = { d: Infinity, y: null as number | null };
  for (let i = 1; i < l.x.length; i++) {
    const ax = l.x[i - 1], az = l.z[i - 1], dx = l.x[i] - ax, dz = l.z[i] - az, len2 = dx * dx + dz * dz;
    const t = len2 > 0 ? Math.max(0, Math.min(1, ((x - ax) * dx + (z - az) * dz) / len2)) : 0;
    const d = Math.hypot(x - ax - dx * t, z - az - dz * t);
    if (d < best.d) {
      const y0 = l.y[i - 1], y1 = l.y[i];
      best = { d, y: y0 !== null && y1 !== null ? y0 + (y1 - y0) * t : null };
    }
  }
  return best;
}

/** The lanes that drive along a structure: at least `minOverlap` metres (or half its length) of the lane lie within
 *  its carriageway (half width + 1.5 m) at its level (2 m, when both heights are known). Returns each lane's arc range
 *  on the structure. */
export function lanesOn(structure: Structure, lanes: readonly Lane[]): { lane: Lane; from: number; to: number }[] {
  const reach = structure.width / 2 + 1.5, need = Math.min(12, structure.length * 0.5);
  const box = [Infinity, Infinity, -Infinity, -Infinity];
  for (const l of structure.lines) for (let i = 0; i < l.x.length; i++) {
    box[0] = Math.min(box[0], l.x[i]); box[1] = Math.min(box[1], l.z[i]); box[2] = Math.max(box[2], l.x[i]); box[3] = Math.max(box[3], l.z[i]);
  }
  const out: { lane: Lane; from: number; to: number }[] = [];
  for (const lane of lanes) {
    const b = lane.bbox;
    if (b[0] > box[2] + reach || b[2] < box[0] - reach || b[1] > box[3] + reach || b[3] < box[1] - reach) continue;
    let from = Infinity, to = -Infinity, inside = 0, last = -1;
    for (let s = 0; s <= lane.length; s += 1) {
      let i = 1; while (i < lane.cum.length - 1 && lane.cum[i] < s) i++;
      const f = Math.max(0, Math.min(1, (s - lane.cum[i - 1]) / ((lane.cum[i] - lane.cum[i - 1]) || 1)));
      const x = lane.points[2 * i - 2] + (lane.points[2 * i] - lane.points[2 * i - 2]) * f;
      const z = lane.points[2 * i - 1] + (lane.points[2 * i + 1] - lane.points[2 * i - 1]) * f;
      const y = lane.ys ? lane.ys[i - 1] + (lane.ys[i] - lane.ys[i - 1]) * f : null;
      const hit = structure.lines.some(l => { const n = nearLine(l, x, z); return n.d <= reach && (y === null || n.y === null || Math.abs(n.y - y) < 2); });
      if (hit) { if (last >= 0 && s - last <= 1.01) inside += 1; from = Math.min(from, s); to = Math.max(to, s); last = s; }
    }
    if (inside >= need) out.push({ lane, from, to });
  }
  return out;
}
