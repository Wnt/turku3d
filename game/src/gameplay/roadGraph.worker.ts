import { mergeRoadGraphs, type RoadGraphJson } from './roadGraph';

const parts = new Map<number, RoadGraphJson>();
self.onmessage = ({ data }: MessageEvent<{ id: number; keys: number[]; additions: [number, RoadGraphJson][] }>) => {
  try {
    const keep = new Set(data.keys);
    for (const key of parts.keys()) if (!keep.has(key)) parts.delete(key);
    for (const [key, part] of data.additions) parts.set(key, part);
    const graph = mergeRoadGraphs(data.keys.map(key => parts.get(key)!));
    // Structured clone, no transfer: merged edges can share typed arrays with the cached parts kept here, and
    // transferring one detached it for the next merge ("ArrayBuffer is detached and could not be cloned").
    self.postMessage({ id: data.id, graph });
  } catch (error) { self.postMessage({ id: data.id, error: String(error) }); }
};
