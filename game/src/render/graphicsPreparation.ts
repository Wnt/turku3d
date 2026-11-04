import type * as THREE from 'three/webgpu';
import { afterRender } from './afterRender';
import { compileStats } from './compileModel';
import { streamTrace } from '../telemetry/streamTrace';

type Job = { model: THREE.Object3D; prepare: () => void | Promise<unknown>; signal?: AbortSignal };
/** Actors (parked and traffic car batches, driven-car keepers: a few programs each) before full tiles, and full tiles
 *  before distant scenery. A job reaching the head while jobs of a higher lane wait goes behind them, so a car body
 *  never waits for a queue of multi-second tile preparations (parked cars were 39-79 s late), and scenery cells and
 *  merged scenery batches never hold a near tile's HD install behind them. The running job is never interrupted. */
export type PreparationPriority = 'actor' | 'tile' | 'scenery';
const LANE: Record<PreparationPriority, number> = { actor: 0, tile: 1, scenery: 2 };

/** One paced queue for incoming models and the resident models skipped by initial-view warmup. */
export class GraphicsPreparation {
  private skipped = new Map<THREE.Object3D, Job>();
  private queued = Promise.resolve();
  private active: Promise<void> | null = null;
  private retiring = Promise.resolve();
  /** Jobs queued and not started yet, per lane (actor, tile, scenery). */
  private waiting = [0, 0, 0];
  constructor(private scene: THREE.Scene, private prepareView: (model: THREE.Object3D) => void | Promise<void>,
    private retireFrame: (release: () => void) => void,
    private nextTask = () => new Promise<void>(resolve => afterRender(resolve))) {}

  defer(model: THREE.Object3D, prepare: Job['prepare'], signal?: AbortSignal) {
    this.skipped.set(model, { model, prepare, signal });
  }

  /** In the scene, or a detached keeper prepared for later draws on purpose (`userData.preparesDetached`). */
  private resident(model: THREE.Object3D) {
    if (model.userData.preparesDetached === true) return true;
    for (let parent: THREE.Object3D | null = model; parent; parent = parent.parent) if (parent === this.scene) return true;
    return false;
  }

  enqueue(model: THREE.Object3D, prepare: Job['prepare'], signal?: AbortSignal, residentOnly = false,
    priority: PreparationPriority = 'tile'): Promise<void> {
    const current = () => !signal?.aborted && (!residentOnly || this.resident(model));
    const queuedAt = performance.now(), lane = LANE[priority];
    this.waiting[lane]++;
    let requeued: Promise<void> | undefined;
    const prepared = this.queued.then(async () => {
      this.waiting[lane]--;
      // Never awaited here: the chain moves on to the higher-lane jobs while this one waits behind them.
      if (this.waiting.slice(0, lane).some(count => count > 0)) {
        requeued = this.enqueue(model, prepare, signal, residentOnly, priority); return;
      }
      // Preparations can visit cross-model resources (notably registered river targets).
      // Recheck because another retirement may join while its preceding fence resolves.
      let barrier: Promise<void>;
      do { barrier = this.retiring; await barrier; } while (barrier !== this.retiring);
      if (!current()) return;
      this.active = prepared;
      try {
        // Preserve the streaming queue's task-after-animation entry and per-draw budgets.
        await this.nextTask();
        if (!current()) return;
        const started = performance.now(), compiles = compileStats.count, compileMs = compileStats.ms;
        await prepare();
        if (current()) await this.prepareView(model);
        streamTrace.event('gfx.prepare', model.userData.traceId as string | undefined, { wait: started - queuedAt, ms: performance.now() - started,
          compiles: compileStats.count - compiles, compileMs: compileStats.ms - compileMs, scenery: !!model.userData.scenery });
      } finally { this.active = null; }
    });
    this.queued = prepared.catch(() => {});
    return prepared.then(() => requeued);
  }

  /** Called after the menu/animation loop is installed; the caller never waits for backfill. */
  flush(onError: (error: unknown) => void) {
    const skipped = [...this.skipped.values()];
    this.skipped.clear();
    // Admit one background model at a time so new streaming jobs can join ahead of the backlog.
    void (async () => {
      for (const { model, prepare, signal } of skipped) {
        try { await this.enqueue(model, prepare, signal, true); }
        catch (error) { onError(error); }
      }
    })();
  }

  /** Complete the active job, retire after a submitted frame, then admit other preparations.
   * Live rendering continues; multiple retirements share the frame rather than the queue backlog. */
  retire(release: () => void) {
    const retired = new Promise<void>(resolve => {
      const schedule = () => this.retireFrame(() => { try { release(); } finally { resolve(); } });
      if (this.active) void this.active.catch(() => {}).then(schedule);
      else schedule();
    });
    this.retiring = Promise.all([this.retiring, retired]).then(() => {});
  }
}
