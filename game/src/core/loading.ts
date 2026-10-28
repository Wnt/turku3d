/** Initial-load instrumentation shared by loaders; bounded and inert after entering the city. */
const active = new Map<number, string>();
const log: string[] = [];
let serial = 0, completed = 0, stopped = false;
let listener: (() => void) | null = null;
export const loading = {
  get running() { return !stopped; },
  get active() { return [...active.values()]; },
  get log() { return log; },
  get completed() { return completed; },
  get total() { return completed + active.size; },
  subscribe(fn: (() => void) | null) { listener = fn; },
  note(message: string) {
    if (stopped) return;
    log.push(message);
    if (log.length > 80) log.shift();
    listener?.();
  },
  begin(label: string) {
    if (stopped) return (_detail?: string) => {};
    const id = ++serial, start = performance.now();
    active.set(id, label);
    listener?.();
    let done = false;
    return (detail = '') => {
      if (done || stopped) return;
      done = true;
      active.delete(id); completed++;
      loading.note(`${label}${detail ? ` · ${detail}` : ''} · ${Math.round(performance.now() - start)} ms`);
    };
  },
  async track<T>(label: string, work: Promise<T>): Promise<T> {
    const done = loading.begin(label);
    try { const result = await work; done(); return result; }
    catch (error) { done(`FAILED: ${String(error)}`); throw error; }
  },
  stop() { stopped = true; active.clear(); listener = null; },
};

export const loadBytes = (bytes: number) => bytes >= 1e6 ? `${(bytes / 1e6).toFixed(2)} MB` : `${(bytes / 1e3).toFixed(1)} kB`;
