import type * as THREE from 'three/webgpu';
import { afterRender } from './afterRender';

type Job = { model: THREE.Object3D; prepare: () => void | Promise<unknown>; signal?: AbortSignal };

/** One paced queue for incoming models and the resident models skipped by initial-view warmup. */
export class GraphicsPreparation {
  private skipped = new Map<THREE.Object3D, Job>();
  private queued = Promise.resolve();
  private active: Promise<void> | null = null;
  private retiring = Promise.resolve();
  constructor(private scene: THREE.Scene, private prepareView: (model: THREE.Object3D) => void | Promise<void>,
    private retireFrame: (release: () => void) => void,
    private nextTask = () => new Promise<void>(resolve => afterRender(resolve))) {}

  defer(model: THREE.Object3D, prepare: Job['prepare'], signal?: AbortSignal) {
    this.skipped.set(model, { model, prepare, signal });
  }

  private resident(model: THREE.Object3D) {
    for (let parent: THREE.Object3D | null = model; parent; parent = parent.parent) if (parent === this.scene) return true;
    return false;
  }

  enqueue(model: THREE.Object3D, prepare: Job['prepare'], signal?: AbortSignal, residentOnly = false): Promise<void> {
    const current = () => !signal?.aborted && (!residentOnly || this.resident(model));
    const prepared = this.queued.then(async () => {
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
        await prepare();
        if (current()) await this.prepareView(model);
      } finally { this.active = null; }
    });
    this.queued = prepared.catch(() => {});
    return prepared;
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
