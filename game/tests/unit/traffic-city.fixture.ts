import { CAR, FOOT, ONEWAY, type RoadGraphJson } from '../../src/gameplay/roadGraph';
import { tileBounds } from '../../src/render/lights/tileLights';

export const STREET = CAR | FOOT | 2 << 3, MAJOR_ONEWAY = CAR | FOOT | ONEWAY | 3 << 3;

/** Axis-aligned streets over x 0..500, z 0..250 (tiles t0_0_0 and t0_1_0), split at every crossing and at the seam
 *  x = 250 as the pipeline clips roads.json per tile. `extra` adds streets to one tile only (a newer version). */
export function city(extra: { tile: string; ways: { from: [number, number]; to: [number, number]; flags: number }[] } | null = null) {
  const streets: { from: [number, number]; to: [number, number]; flags: number }[] = [
    { from: [0, 100], to: [500, 100], flags: STREET },
    { from: [30, 180], to: [470, 180], flags: STREET },
    { from: [480, 40], to: [20, 40], flags: MAJOR_ONEWAY },
    { from: [80, 0], to: [80, 250], flags: STREET },
    { from: [170, 40], to: [170, 230], flags: STREET },
    { from: [330, 0], to: [330, 250], flags: STREET },
    { from: [420, 20], to: [420, 240], flags: STREET },
  ];
  const tiles = new Map<string, { nodes: number[]; edges: number[][] }>();
  const add = (tile: string, a: [number, number], b: [number, number], flags: number) => {
    const t = tiles.get(tile) ?? { nodes: [], edges: [] };
    tiles.set(tile, t);
    const node = (p: [number, number]) => {
      for (let i = 0; i < t.nodes.length; i += 2) if (Math.hypot(t.nodes[i] - p[0], t.nodes[i + 1] - p[1]) < 0.05) return i / 2;
      t.nodes.push(p[0], p[1]); return t.nodes.length / 2 - 1;
    };
    t.edges.push([node(a), node(b), flags, Math.hypot(b[0] - a[0], b[1] - a[1])]);
  };
  const cut = (s: { from: [number, number]; to: [number, number]; flags: number }, all: typeof streets) => {
    const [x0, z0] = s.from, [x1, z1] = s.to, horizontal = z0 === z1;
    const stops = new Set<number>([horizontal ? x0 : z0, horizontal ? x1 : z1]);
    for (const o of all) {
      if (o === s || (o.from[1] === o.to[1]) === horizontal) continue;
      const c = horizontal ? o.from[0] : o.from[1];
      const [lo, hi] = horizontal ? [Math.min(o.from[1], o.to[1]), Math.max(o.from[1], o.to[1])] : [Math.min(o.from[0], o.to[0]), Math.max(o.from[0], o.to[0])];
      const along = horizontal ? z0 : x0;
      if (along >= lo && along <= hi && c > Math.min(horizontal ? x0 : z0, horizontal ? x1 : z1) && c < Math.max(horizontal ? x0 : z0, horizontal ? x1 : z1)) stops.add(c);
    }
    if (horizontal && Math.min(x0, x1) < 250 && Math.max(x0, x1) > 250) stops.add(250);
    const sorted = [...stops].sort((a, b) => a - b);
    if ((horizontal ? x1 < x0 : z1 < z0)) sorted.reverse();
    const point = (v: number): [number, number] => horizontal ? [v, z0] : [x0, v];
    for (let i = 1; i < sorted.length; i++) {
      const a = point(sorted[i - 1]), b = point(sorted[i]);
      add(`t0_${Math.floor((a[0] + b[0]) / 2 / 250)}_0`, a, b, s.flags);
    }
  };
  for (const s of streets) cut(s, streets);
  for (const s of extra?.ways ?? []) add(extra!.tile, s.from, s.to, s.flags);
  const json = new Map<string, RoadGraphJson>();
  for (const [tile, t] of tiles) json.set(tile, { format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz',
    bbox: [...tileBounds(tile)!] as [number, number, number, number], nodes: t.nodes, edges: t.edges });
  return json;
}
