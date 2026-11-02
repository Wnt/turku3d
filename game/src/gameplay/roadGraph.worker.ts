import { mergeRoadGraphs, type RoadGraphJson } from './roadGraph';

const parts = new Map<number, RoadGraphJson>();
self.onmessage = ({ data }: MessageEvent<{ id: number; keys: number[]; additions: [number, RoadGraphJson][] }>) => {
  try {
    const keep = new Set(data.keys);
    for (const key of parts.keys()) if (!keep.has(key)) parts.delete(key);
    for (const [key, part] of data.additions) parts.set(key, part);
    const graph = mergeRoadGraphs(data.keys.map(key => parts.get(key)!));
    const buffers = new Set<ArrayBuffer>([graph.nodes.buffer as ArrayBuffer, graph.nodeFlags.buffer as ArrayBuffer]);
    for (const edge of graph.edges) for (const points of [edge.points, edge.cumulative, edge.heights]) {
      if (points) buffers.add(points.buffer as ArrayBuffer);
    }
    self.postMessage({ id: data.id, graph }, { transfer: [...buffers] });
  } catch (error) { self.postMessage({ id: data.id, error: String(error) }); }
};
