import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { GraphicsPreparation } from '../../src/render/graphicsPreparation';
import { FrameRetirement } from '../../src/render/retirement';

const turn = () => new Promise<void>(resolve => setTimeout(resolve, 0));
const pending = () => {
  let resolve!: () => void;
  const promise = new Promise<void>(done => { resolve = done; });
  return { promise, resolve };
};

test('startup is nonblocking; resident offscreen models warm once after Ready', async () => {
  const scene = new THREE.Scene(), model = new THREE.Group(); scene.add(model);
  const view = vi.fn(), stale = vi.fn(), latest = vi.fn(), errors = vi.fn();
  const queue = new GraphicsPreparation(scene, view, () => {}, async () => {});
  queue.defer(model, stale); queue.defer(model, latest);
  await turn(); expect(view).not.toHaveBeenCalled(); expect(latest).not.toHaveBeenCalled();
  queue.flush(errors);
  expect(view).not.toHaveBeenCalled(); // The menu caller does not await even the first model.
  await turn();
  expect(stale).not.toHaveBeenCalled(); expect(latest).toHaveBeenCalledOnce(); expect(view).toHaveBeenCalledWith(model);
  queue.flush(errors); await turn(); expect(view).toHaveBeenCalledOnce(); expect(errors).not.toHaveBeenCalled();
});

test('backfill rejects aborted, removed and detached-session models, including removal during its frame wait', async () => {
  const scene = new THREE.Scene(), live = new THREE.Group(), removed = new THREE.Group(), session = new THREE.Group();
  const child = new THREE.Group(), aborted = new THREE.Group(), signal = new AbortController();
  session.add(child); scene.add(live, removed, session, aborted);
  const frame = pending(), prepare = vi.fn(), view = vi.fn(), errors = vi.fn();
  const queue = new GraphicsPreparation(scene, view, () => {}, () => frame.promise);
  for (const model of [live, removed, child]) queue.defer(model, prepare);
  queue.defer(aborted, prepare, signal.signal);
  removed.removeFromParent(); session.removeFromParent(); signal.abort();
  queue.flush(errors); await turn(); live.removeFromParent(); frame.resolve(); await turn();
  expect(prepare).not.toHaveBeenCalled(); expect(view).not.toHaveBeenCalled(); expect(errors).not.toHaveBeenCalled();
});

test('incoming unpublished models retain the streaming path and go ahead of the remaining backfill', async () => {
  const scene = new THREE.Scene(), a = new THREE.Group(), b = new THREE.Group(); scene.add(a, b);
  const first = pending(), events: string[] = [];
  const queue = new GraphicsPreparation(scene, () => {}, () => {}, async () => {});
  queue.defer(a, async () => { events.push('a'); await first.promise; });
  queue.defer(b, () => { events.push('b'); });
  queue.flush(e => { throw e; }); await turn();
  const incoming = queue.enqueue(new THREE.Group(), () => { events.push('incoming'); });
  first.resolve(); await incoming; await turn(); expect(events).toEqual(['a', 'incoming', 'b']);
});

test('detached resources retire after the active job and frame, before another model starts', async () => {
  const scene = new THREE.Scene(), a = new THREE.Group(), b = new THREE.Group(); scene.add(a, b);
  const first = pending(), second = pending(), releases: (() => void)[] = [], released = vi.fn(), view = vi.fn(), next = vi.fn(() => second.promise);
  const queue = new GraphicsPreparation(scene, view, release => releases.push(release), async () => {});
  queue.defer(a, () => first.promise); queue.defer(b, next);
  queue.flush(e => { throw e; }); await turn();
  a.removeFromParent(); queue.retire(released);
  expect(releases).toHaveLength(0); first.resolve(); await turn();
  expect(releases).toHaveLength(1); expect(view).not.toHaveBeenCalledWith(a);
  expect(next).not.toHaveBeenCalled();
  releases[0](); expect(released).toHaveBeenCalledOnce(); expect(view).not.toHaveBeenCalledWith(b);
  second.resolve(); await turn(); expect(view).toHaveBeenCalledWith(b);
});

test('a delayed GPU retirement prevents the next compile from visiting a retiring river target', async () => {
  const scene = new THREE.Scene(), frame = new FrameRetirement(), first = pending(), gpu = pending();
  const river = { registered: true, disposed: false }, accesses: boolean[] = [];
  const queue = new GraphicsPreparation(scene, () => {}, release => frame.retire(release), async () => {});
  const a = queue.enqueue(new THREE.Group(), () => first.promise); await turn();
  queue.retire(() => { river.registered = false; river.disposed = true; });
  const b = queue.enqueue(new THREE.Group(), () => { if (river.registered) accesses.push(river.disposed); });
  first.resolve(); await a; await turn();
  const submitted = frame.submitted(() => gpu.promise); await turn();
  expect(accesses).toEqual([]); expect(river.disposed).toBe(false);
  gpu.resolve(); await submitted; await b;
  expect(river.disposed).toBe(true); expect(accesses).toEqual([]);
});

test('several retirements share one GPU frame and a retirement joining its fence also blocks admission', async () => {
  const frame = new FrameRetirement(), gpu = pending(), secondGpu = pending(), started = vi.fn(), released = vi.fn();
  const queue = new GraphicsPreparation(new THREE.Scene(), () => {}, release => frame.retire(release), async () => {});
  queue.retire(released); queue.retire(released);
  const submitted = frame.submitted(() => gpu.promise);
  queue.retire(released);
  const prepared = queue.enqueue(new THREE.Group(), started); await turn();
  gpu.resolve(); await submitted; await turn();
  expect(released).toHaveBeenCalledTimes(2); expect(started).not.toHaveBeenCalled();
  const next = frame.submitted(() => secondGpu.promise);
  secondGpu.resolve(); await next; await prepared;
  expect(released).toHaveBeenCalledTimes(3); expect(started).toHaveBeenCalledOnce();
});

test('backfill failures are reported and do not strand the queue or resource retirement', async () => {
  const scene = new THREE.Scene(), a = new THREE.Group(), b = new THREE.Group(); scene.add(a, b);
  const failure = new Error('compile failed'), errors = vi.fn(), view = vi.fn(), retire = vi.fn();
  const queue = new GraphicsPreparation(scene, view, retire, async () => {});
  queue.defer(a, () => { throw failure; }); queue.defer(b, async () => {});
  queue.flush(errors); await turn(); expect(errors).toHaveBeenCalledWith(failure); expect(view).toHaveBeenCalledWith(b);
  const release = vi.fn(); queue.retire(release); expect(retire).toHaveBeenCalledOnce();
  retire.mock.calls[0][0](); expect(release).toHaveBeenCalledOnce();
});

test('an abort during preparation skips view compilation but still finishes the serialized job', async () => {
  const signal = new AbortController(), view = vi.fn(), queue = new GraphicsPreparation(new THREE.Scene(), view, () => {}, async () => {});
  await queue.enqueue(new THREE.Group(), () => signal.abort(), signal.signal);
  expect(view).not.toHaveBeenCalled();
});

test('a scenery job reaching the head while full-tile jobs wait goes behind them, keeping tile order', async () => {
  const scene = new THREE.Scene(), first = pending(), events: string[] = [];
  const queue = new GraphicsPreparation(scene, () => {}, () => {}, async () => {});
  const head = queue.enqueue(new THREE.Group(), async () => { events.push('tile-a'); await first.promise; });
  const scenery = queue.enqueue(new THREE.Group(), () => { events.push('scenery'); }, undefined, false, 'scenery');
  const b = queue.enqueue(new THREE.Group(), () => { events.push('tile-b'); });
  const c = queue.enqueue(new THREE.Group(), () => { events.push('tile-c'); });
  first.resolve();
  await Promise.all([head, scenery, b, c]);
  expect(events).toEqual(['tile-a', 'tile-b', 'tile-c', 'scenery']);
  // With no tile waiting, scenery runs in its own turn.
  await queue.enqueue(new THREE.Group(), () => { events.push('scenery-2'); }, undefined, false, 'scenery');
  expect(events.at(-1)).toBe('scenery-2');
});
