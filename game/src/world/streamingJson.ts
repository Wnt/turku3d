import { streamingBudget } from '../render/preparationBudget';
import { applyJsonRecords, type JsonPeek } from './glbRecords';
export type { JsonPeek } from './glbRecords';

const budget = streamingBudget();
let worker: Worker | undefined, sequence = 0;
const pending = new Map<number, { objects: Record<string, unknown>[]; resolve: (value: unknown) => void; reject: (error: Error) => void }>();

function streamingWorker() {
  if (!worker) {
    worker = new Worker(new URL('./streamingJson.worker.ts', import.meta.url), { type: 'module' });
    worker.onmessage = async ({ data }) => {
      const job = pending.get(data.id);
      if (!job) return;
      if (data.error) { pending.delete(data.id); job.reject(new Error(data.error)); return; }
      if (data.peek) { pending.delete(data.id); job.resolve(data.peek); return; }
      const pause = budget.checkpoint();
      if (pause) await pause;
      applyJsonRecords(job.objects, data.rows);
      if (data.done) { pending.delete(data.id); job.resolve(job.objects[0]); }
      else worker?.postMessage({ id: data.id });
    };
    worker.onerror = event => {
      for (const job of pending.values()) job.reject(new Error(event.message));
      pending.clear(); worker?.terminate(); worker = undefined;
    };
  }
  return worker;
}

/** Parsing and structured-clone delivery are both bounded away from the render loop. */
export function parseStreamingJson<T>(buffer: ArrayBuffer): Promise<T> {
  const target = streamingWorker(), id = ++sequence;
  return new Promise<T>((resolve, reject) => {
    pending.set(id, { objects: [{}], resolve: value => resolve(value as T), reject });
    target.postMessage({ id, buffer }, [buffer]);
  });
}

/** Parse in the worker and deliver only `peekJson`: one small reply that needs no main-thread record budget. */
export function peekStreamingJson(buffer: ArrayBuffer): Promise<JsonPeek> {
  const target = streamingWorker(), id = ++sequence;
  return new Promise<JsonPeek>((resolve, reject) => {
    pending.set(id, { objects: [{}], resolve: value => resolve(value as JsonPeek), reject });
    target.postMessage({ id, buffer, peek: true }, [buffer]);
  });
}

export async function readStreamingJson<T>(response: Response): Promise<T> {
  // Node-side service fixtures have no browser Worker or byte body.
  if (typeof Worker === 'undefined') return response.json() as Promise<T>;
  return parseStreamingJson<T>(await response.arrayBuffer());
}
