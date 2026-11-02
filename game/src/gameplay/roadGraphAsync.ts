import { mergeRoadGraphs, type RoadGraph, type RoadGraphJson } from './roadGraph';

let worker: Worker | undefined, sequence = 0, partSequence = 0;
const ids = new WeakMap<RoadGraphJson, number>();
const known = new Set<number>();
const pending = new Map<number, { resolve: (graph: RoadGraph) => void; reject: (error: Error) => void }>();

/** Only new tile fragments cross to the worker; unchanged parts are neither cloned nor decoded again. */
export function mergeRoadGraphsAsync(parts: readonly RoadGraphJson[]): Promise<RoadGraph> {
  if (typeof Worker === 'undefined') return Promise.resolve(mergeRoadGraphs(parts));
  if (!worker) {
    worker = new Worker(new URL('./roadGraph.worker.ts', import.meta.url), { type: 'module' });
    worker.onmessage = ({ data }) => {
      const job = pending.get(data.id);
      if (!job) return;
      pending.delete(data.id);
      if (data.error) job.reject(new Error(data.error)); else job.resolve(data.graph);
    };
    worker.onerror = event => {
      for (const job of pending.values()) job.reject(new Error(event.message));
      pending.clear(); known.clear(); worker?.terminate(); worker = undefined;
    };
  }
  const additions: [number, RoadGraphJson][] = [];
  const keys = parts.map(part => {
    let id = ids.get(part);
    if (id === undefined) { id = ++partSequence; ids.set(part, id); }
    if (!known.has(id)) { known.add(id); additions.push([id, part]); }
    return id;
  });
  const keep = new Set(keys);
  for (const key of known) if (!keep.has(key)) known.delete(key);
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    worker!.postMessage({ id, keys, additions });
  });
}
