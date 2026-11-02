import { PreparationBudget } from '../render/preparationBudget';
import { applyJsonRecords } from './glbRecords';

const budget = new PreparationBudget();
let worker: Worker | undefined, sequence = 0;
const pending = new Map<number, { objects: Record<string, unknown>[]; resolve: (value: unknown) => void; reject: (error: Error) => void }>();

/** Parsing and structured-clone delivery are both bounded away from the render loop. */
export function parseStreamingJson<T>(buffer: ArrayBuffer): Promise<T> {
  if (!worker) {
    worker = new Worker(new URL('./streamingJson.worker.ts', import.meta.url), { type: 'module' });
    worker.onmessage = async ({ data }) => {
      const job = pending.get(data.id);
      if (!job) return;
      if (data.error) { pending.delete(data.id); job.reject(new Error(data.error)); return; }
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
  const id = ++sequence;
  return new Promise<T>((resolve, reject) => {
    pending.set(id, { objects: [{}], resolve: value => resolve(value as T), reject });
    worker!.postMessage({ id, buffer }, [buffer]);
  });
}

export async function readStreamingJson<T>(response: Response): Promise<T> {
  // Node-side service fixtures have no browser Worker or byte body.
  if (typeof Worker === 'undefined') return response.json() as Promise<T>;
  return parseStreamingJson<T>(await response.arrayBuffer());
}
