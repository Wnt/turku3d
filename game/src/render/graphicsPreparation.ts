import type * as THREE from 'three/webgpu';
import { afterRender } from './afterRender';
import { compileStats } from './compileModel';
import { streamTrace } from '../telemetry/streamTrace';

type Job = { model: THREE.Object3D; prepare: () => void | Promise<unknown>; signal?: AbortSignal };
/** Where a streamed model's job stands among the others of its lane: `rank()` (lower first, read when the queue
 *  picks its next job; seconds until the player reaches the tile, world/coarseTiles.ts) and `live()` (false once the
 *  model's tile was unloaded or its install given up: the job is dropped unrun). Jobs without an order rank 0. */
export interface PreparationOrder { rank?: () => number; live?: () => boolean }
/** Actors (parked and traffic car batches, driven-car keepers: a few programs each) before full tiles, and full tiles
 *  before distant scenery. The queue always picks from the highest lane that has jobs, so a car body never waits for
 *  a queue of multi-second tile preparations (parked cars were 39-79 s late), and scenery cells and merged scenery
 *  batches never hold a near tile's HD install behind them. Within a lane the lowest rank goes first, ties in arrival
 *  order: at 85 km/h the tile lane held 30-45 jobs in arrival order, and the window's own tiles waited behind tiles the
 *  player had already passed (fastdrive fly, 2026-11-08). The running job is never interrupted. */
export type PreparationPriority = 'actor' | 'tile' | 'scenery';
/** A job that has gone behind higher lanes for this long (ms) runs at its next turn: on a slow GPU (the Air) the
 *  tile lane never empties while streaming, and scenery cells waited behind it forever (city.lab: 0 of ~180 distant
 *  cells after minutes). One aged job per turn keeps the near tiles first in all other cases. */
const MAX_DEFER_MS = 4000;
const LANE: Record<PreparationPriority, number> = { actor: 0, tile: 1, scenery: 2 };
const RECENT = 64;
/** A finished job (diagnostics): wall ms, lane, model, compileModel draws handed to r186 (`calls`), node builds among
 *  them and their wall ms, the wall ms of the cached draws, ms spent waiting for a budget window, ms queued. */
interface Queued { model: THREE.Object3D; prepare: Job['prepare']; signal?: AbortSignal; residentOnly: boolean; lane: number;
  order?: PreparationOrder; since: number; seq: number; resolve: () => void; reject: (error: unknown) => void }
interface JobStats { ms: number; lane: number; model: string; calls: number; builds: number; buildMs: number; hitMs: number; waitMs: number; wait: number }

/** Resolves at `signal`'s abort; `off()` takes the listener away again. The signal is the city's and outlives every
 *  job: a listener left on it (`once` only removes it when the abort fires) kept the job's closures and so every model
 *  ever prepared alive, with its geometry arrays and texture mips: the page grew ~4 MB/s while driving on the M1 Max
 *  (framecheck mac-buf-1: 2.2 GB of the 3.8 GB of ArrayBuffers held by AbortSignal listeners after 7 minutes). */
function abortion(signal: AbortSignal) {
  let off = () => {};
  const promise = new Promise<void>(resolve => {
    if (signal.aborted) { resolve(); return; }
    const listener = () => resolve();
    signal.addEventListener('abort', listener, { once: true });
    off = () => signal.removeEventListener('abort', listener);
  });
  return { promise, off };
}

/** A model's name for diagnostics: its trace id (tile or cell), name and size. */
function describe(model: THREE.Object3D) {
  let meshes = 0;
  model.traverse(o => { if ((o as THREE.Mesh).isMesh) meshes++; });
  return `${model.userData.traceId ?? ''}|${model.name || model.type}|${model.userData.scenery ? 'scenery' : ''}|meshes=${meshes}`;
}

/** One paced queue for incoming models and the resident models skipped by initial-view warmup. */
export class GraphicsPreparation {
  private skipped = new Map<THREE.Object3D, Job>();
  private jobs: Queued[] = [];
  private seq = 0;
  private running = false;
  /** The last job taken had gone behind higher lanes for MAX_DEFER_MS: the next one comes from the highest lane. */
  private tookAged = false;
  private active: Promise<void> | null = null;
  private retiring = Promise.resolve();
  /** Jobs queued and not started yet, per lane (actor, tile, scenery). */
  private waiting = [0, 0, 0];
  /** Jobs finished (prepared, skipped, dropped or aborted) and the start of the job running now (diagnostics: a
   *  stalled queue shows a growing wait with an old start); `dropped` the jobs of unloaded tiles never run. */
  private finished = 0;
  private dropped = 0;
  private activeSince: number | null = null;
  private activeModel: THREE.Object3D | null = null;
  /** The slowest jobs so far: which preparation holds the queue. */
  private slowest: JobStats[] = [];
  /** The last RECENT jobs that ran (diagnostics). */
  private recent: JobStats[] = [];
  stats() {
    const m = this.activeModel;
    return { waiting: [...this.waiting], finished: this.finished, dropped: this.dropped,
      activeMs: this.activeSince === null ? null : performance.now() - this.activeSince,
      active: m ? describe(m) : null, slowest: this.slowest.slice(), recent: this.recent.slice() };
  }
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
    priority: PreparationPriority = 'tile', order?: PreparationOrder): Promise<void> {
    const lane = LANE[priority];
    const done = new Promise<void>((resolve, reject) => {
      this.jobs.push({ model, prepare, signal, residentOnly, lane, order, since: performance.now(), seq: this.seq++, resolve, reject });
    });
    this.waiting[lane]++;
    void this.pump();
    if (!signal) return done;
    // Its waiter (a released city's install) goes on at the abort, not when the queue reaches the job.
    const abort = abortion(signal);
    return Promise.race([done, abort.promise]).finally(abort.off);
  }

  /** The next job: an aborted or dropped job first (it leaves at once), then a job that has gone behind higher lanes
   *  for MAX_DEFER_MS (never two in a row), else the lowest rank of the highest lane, ties in arrival order. */
  private take(): Queued | undefined {
    const jobs = this.jobs;
    if (!jobs.length) return undefined;
    let pick = jobs.findIndex(job => job.signal?.aborted || (job.order?.live !== undefined && !job.order.live()));
    if (pick < 0) {
      const now = performance.now();
      let top = Infinity;
      for (const job of jobs) top = Math.min(top, job.lane);
      if (!this.tookAged) {
        for (let i = 0; i < jobs.length; i++)
          if (jobs[i].lane > top && now - jobs[i].since >= MAX_DEFER_MS && (pick < 0 || jobs[i].seq < jobs[pick].seq)) pick = i;
      }
      this.tookAged = pick >= 0;
      if (pick < 0) {
        let best = Infinity;
        for (let i = 0; i < jobs.length; i++) {
          if (jobs[i].lane !== top) continue;
          const rank = jobs[i].order?.rank?.() ?? 0;
          if (pick < 0 || rank < best || (rank === best && jobs[i].seq < jobs[pick].seq)) { best = rank; pick = i; }
        }
      }
    }
    const [job] = jobs.splice(pick, 1);
    this.waiting[job.lane]--;
    return job;
  }

  private async pump() {
    if (this.running) return;
    this.running = true;
    try {
      // Jobs enqueued in the same task as the first one compete with it for the first turn.
      await Promise.resolve();
      for (let job = this.take(); job; job = this.take()) await this.run(job);
    } finally { this.running = false; }
  }

  private async run(job: Queued) {
    const { model, signal, residentOnly, order } = job;
    const live = () => !signal?.aborted && (!order?.live || order.live());
    const current = () => live() && (!residentOnly || this.resident(model));
    if (!live()) {
      // An aborted job, or one whose tile was unloaded or given up while it waited, leaves without running.
      if (!signal?.aborted) this.dropped++;
      this.finished++; job.resolve(); return;
    }
    try {
      // Preparations can visit cross-model resources (notably registered river targets).
      // Recheck because another retirement may join while its preceding fence resolves.
      let barrier: Promise<void>;
      do { barrier = this.retiring; await barrier; } while (barrier !== this.retiring);
      if (!current()) { if (!signal?.aborted) this.dropped++; this.finished++; job.resolve(); return; }
      const work = this.work(job, current);
      this.active = work;
      await work;
      job.resolve();
    } catch (error) { job.reject(error); }
  }

  private async work({ model, prepare, signal, lane, since }: Queued, current: () => boolean) {
    this.activeSince = performance.now(); this.activeModel = model;
    const calls = compileStats.calls, waitMs = compileStats.waitMs, builds = compileStats.builds, buildMs = compileStats.buildMs, hitMs = compileStats.hitMs;
    // An aborted job (its city was released) stops waiting at once: a preparation of models that are gone may
    // never settle, and the whole queue waits behind it.
    const aborted = signal ? abortion(signal) : null;
    const until = (work: Promise<unknown>) => aborted ? Promise.race([work, aborted.promise]) : work;
    try {
      // Preserve the streaming queue's task-after-animation entry and per-draw budgets.
      await until(this.nextTask());
      if (!current()) return;
      const started = performance.now(), compiles = compileStats.count, compileMs = compileStats.ms;
      await until(Promise.resolve(prepare()));
      if (current()) await until(Promise.resolve(this.prepareView(model)));
      streamTrace.event('gfx.prepare', model.userData.traceId as string | undefined, { wait: started - since, ms: performance.now() - started,
        compiles: compileStats.count - compiles, compileMs: compileStats.ms - compileMs, scenery: !!model.userData.scenery });
    } finally {
      aborted?.off();
      const ms = performance.now() - this.activeSince!;
      const row: JobStats = { ms: Math.round(ms), lane, model: describe(model), calls: compileStats.calls - calls, builds: compileStats.builds - builds,
        buildMs: Math.round(compileStats.buildMs - buildMs), hitMs: Math.round(compileStats.hitMs - hitMs),
        waitMs: Math.round(compileStats.waitMs - waitMs), wait: Math.round(this.activeSince! - since) };
      this.recent.push(row); if (this.recent.length > RECENT) this.recent.shift();
      if (ms > 1000) { this.slowest.push(row); this.slowest.sort((a, b) => b.ms - a.ms); this.slowest.length = Math.min(this.slowest.length, 8); }
      this.active = null; this.activeSince = null; this.activeModel = null; this.finished++;
    }
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
