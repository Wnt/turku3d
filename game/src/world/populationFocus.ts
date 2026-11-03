import type { RoadGraphJson } from '../gameplay/roadGraph';

/** Surveyed road elevations at edge ends, x/z/y per sample, once per graph (graphs are replaced, never edited). */
const samples = new WeakMap<RoadGraphJson, Float64Array>();
function endpoints(graph: RoadGraphJson): Float64Array {
  let out = samples.get(graph);
  if (out) return out;
  const values: number[] = [];
  graph.edges.forEach((edge, i) => {
    const heights = graph.edgeHeights?.[i]; if (!heights) return;
    for (const [node, y] of [[edge[0], heights[0]], [edge[1], heights[heights.length - 1]]]) {
      if (Number.isFinite(y)) values.push(graph.nodes[node * 2], graph.nodes[node * 2 + 1], y);
    }
  });
  out = Float64Array.from(values);
  samples.set(graph, out);
  return out;
}

/** A high camera sees the street ahead, not the patch directly under its eye. Keep street-level rings
 * unchanged, but centre aerial populations on the visible ground using surveyed road elevations. */
export function populationFocus(eye: { x: number; y: number; z: number }, forward: { x: number; y: number; z: number },
  graphs: readonly RoadGraphJson[]) {
  let best = Infinity, ground = eye.y;
  for (const graph of graphs) {
    if (graph.bbox) {
      const [x0, z0, x1, z1] = graph.bbox;
      const dx = Math.max(x0 - eye.x, 0, eye.x - x1), dz = Math.max(z0 - eye.z, 0, eye.z - z1);
      if (dx * dx + dz * dz > best) continue;
    }
    const p = endpoints(graph);
    for (let i = 0; i < p.length; i += 3) {
      const d = (p[i] - eye.x) ** 2 + (p[i + 1] - eye.z) ** 2;
      if (d < best) { best = d; ground = p[i + 2]; }
    }
  }
  const flat = Math.hypot(forward.x, forward.z) || 1;
  const lead = Math.min(300, Math.max(0, eye.y - ground - 20) / Math.max(.25, -forward.y / flat));
  return { x: eye.x + forward.x / flat * lead, z: eye.z + forward.z / flat * lead };
}
