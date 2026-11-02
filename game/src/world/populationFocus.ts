import type { RoadGraphJson } from '../gameplay/roadGraph';

/** A high camera sees the street ahead, not the patch directly under its eye. Keep street-level rings
 * unchanged, but centre aerial populations on the visible ground using surveyed road elevations. */
export function populationFocus(eye: { x: number; y: number; z: number }, forward: { x: number; y: number; z: number },
  graphs: readonly RoadGraphJson[]) {
  let best = Infinity, ground = eye.y;
  for (const graph of graphs) graph.edges.forEach((edge, i) => {
    const heights = graph.edgeHeights?.[i]; if (!heights) return;
    for (const [node, y] of [[edge[0], heights[0]], [edge[1], heights[heights.length - 1]]]) {
      if (!Number.isFinite(y)) continue;
      const d = (graph.nodes[node * 2] - eye.x) ** 2 + (graph.nodes[node * 2 + 1] - eye.z) ** 2;
      if (d < best) { best = d; ground = y; }
    }
  });
  const flat = Math.hypot(forward.x, forward.z) || 1;
  const lead = Math.min(300, Math.max(0, eye.y - ground - 20) / Math.max(.25, -forward.y / flat));
  return { x: eye.x + forward.x / flat * lead, z: eye.z + forward.z / flat * lead };
}
