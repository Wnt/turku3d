import { afterRender } from './afterRender';

/** A window opened longer ago than this (ms) belongs to an earlier frame. */
const STALE_MS = 16;
const nextTask = () => new Promise<void>(resolve => setTimeout(resolve, 0));

/** Shader preparation shares a frame budget across streamed models and render passes. Individual draws are atomic.
 *  `resumeStale`: sporadic work (a worker reply or fetch arriving long after the window opened) starts a new window
 *  after one task hop instead of waiting for the next drawn frame; still never inside an animation callback. */
export class PreparationBudget {
  private started: number | null = null;
  private pending: Promise<void> | null = null;
  constructor(private now = () => performance.now(), private nextFrame = () => new Promise<void>(resolve => afterRender(resolve)),
    private resumeStale: (() => Promise<void>) | null = null) {}
  checkpoint(): Promise<void> | undefined {
    this.started ??= this.now();
    if (this.pending) return this.pending;
    const elapsed = this.now() - this.started;
    if (elapsed < 2) return;
    const wait = this.resumeStale && elapsed > STALE_MS ? this.resumeStale() : this.nextFrame();
    this.pending = wait.then(() => { this.started = this.now(); this.pending = null; });
    return this.pending;
  }
}

/** A streaming install budget: 2 ms windows, and sporadic steps do not wait a whole frame. */
export function streamingBudget() {
  return new PreparationBudget(undefined, undefined, nextTask);
}
