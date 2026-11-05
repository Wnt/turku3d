import { afterRender } from './afterRender';

/** r186 splits every asynchronous shader build (NodeBuilder.buildAsync, per stage) and compileAsync (per draw) with
 * `scheduler.yield()`. Those continuations run at high priority as soon as the current task ends, so a streaming
 * preparation chained several 10–30 ms build stages back to back between two frames: on the Radeon Pro 560X they
 * were in 132 of 135 hitches over 50 ms (LoAF `Scheduler.yield.then` in three.core, 13 ms each on average).
 *
 * During play, `self.scheduler.yield` instead queues the continuation and releases queued ones after each frame
 * while that gap has used under `budgetMs()` (main.ts: more while the 3x3 window still waits for full tiles); a stage that runs over ends the gap's share, so at most one long stage
 * lands between two frames. Loading, the bench settle, hidden pages and every other caller outside play keep the
 * browser's own scheduler.yield, so warm-up compiles are as fast as before. */
export function paceYields(playing: () => boolean, budgetMs: () => number = () => 3) {
  const scheduler = (globalThis as { scheduler?: { yield?: () => Promise<void> } }).scheduler;
  if (!scheduler?.yield) return null;
  const native = scheduler.yield.bind(scheduler);
  const queue: (() => void)[] = [];
  let gapStart = -Infinity, waiting = false, chained = false, released = 0;
  const live = () => playing() && document.visibilityState === 'visible';
  const step = () => {
    if (!queue.length) return;
    if (!live()) { while (queue.length) queue.shift()!(); return; }
    if (performance.now() - gapStart >= budgetMs()) { nextGap(); return; }
    released++;
    queue.shift()!();
    // The continuation runs in this task's microtasks; the next step measures after it.
    chain();
  };
  // The next continuation of the same gap runs in this task (a microtask), not in a new one: on a CPU-bound machine a
  // frame lands between any two tasks, so one continuation per task gave shader builds only the time frames left
  // (M1 Air: ~7 deferred builds/s whatever the share, visible meshes waiting 18 s). Within the share the work is
  // continuous; a continuation that has not yielded yet when the queue runs dry re-chains on its own yield.
  const chain = () => { chained = true; queueMicrotask(() => { chained = false; step(); }); };
  const nextGap = () => {
    if (waiting) return;
    waiting = true;
    afterRender(() => { waiting = false; gapStart = performance.now(); step(); });
  };
  scheduler.yield = () => {
    if (!live()) return native();
    return new Promise<void>(resolve => {
      queue.push(resolve);
      if (chained) return;
      // Still inside this gap's share (the next frame is further away than any share): continue now. Waiting a frame
      // for every yield made each compileAsync of a tile's preparation cost a frame, cached programs included, so one
      // tile's prepare took 10-17 s and held the serial preparation queue (parked cars 39-79 s late).
      if (!waiting && performance.now() - gapStart < budgetMs()) chain();
      else nextGap();
    });
  };
  return {
    get queued() { return queue.length; },
    get released() { return released; },
    release() { scheduler.yield = native; while (queue.length) queue.shift()!(); },
  };
}
