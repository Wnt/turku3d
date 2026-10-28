/** Loading UI contract. Bytes count completed same-origin HTTP response bodies (compressed on the wire).
 * Totals are discovered from the tileset/manifest, never a timer or a fabricated percentage. */
interface LoadProgress {
  phase: 'loading' | 'compiling' | 'ready' | 'error';
  elapsedMs: number;
  bytesLoaded: number;
  visual: { loaded: number; total: number };
  textures: { loaded: number; pending: number };
  collision: { loaded: number; total: number };
  compile: { done: number; total: number };
  errors: string[];
}

class WorldLoad {
  private started = performance.now();
  private finished: number | null = null;
  private state: LoadProgress = { phase: 'loading', elapsedMs: 0, bytesLoaded: 0,
    visual: { loaded: 0, total: 0 }, textures: { loaded: 0, pending: 0 },
    collision: { loaded: 0, total: 0 }, compile: { done: 0, total: 0 }, errors: [] };

  constructor() {
    new PerformanceObserver(list => {
      for (const entry of list.getEntries() as PerformanceResourceTiming[]) this.state.bytesLoaded += entry.encodedBodySize;
    }).observe({ type: 'resource', buffered: true });
  }

  update(value: Partial<Omit<LoadProgress, 'elapsedMs' | 'bytesLoaded'>>) {
    Object.assign(this.state, value);
    if (value.phase === 'ready' || value.phase === 'error') this.finished = performance.now();
  }

  /** A detached snapshot; safe to poll from a loading screen or a headless probe. */
  snapshot(): LoadProgress {
    return { ...this.state, elapsedMs: Math.round((this.finished ?? performance.now()) - this.started),
      visual: { ...this.state.visual }, textures: { ...this.state.textures }, collision: { ...this.state.collision },
      compile: { ...this.state.compile }, errors: [...this.state.errors] };
  }
}

export const worldLoad = new WorldLoad();
