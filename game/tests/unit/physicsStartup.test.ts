import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { PhysicsClient } from '../../src/physics/client';
import type { FromWorker } from '../../src/physics/protocol';

class FakeWorker {
  static instances: FakeWorker[] = [];
  onmessage: ((event: { data: FromWorker }) => void) | null = null;
  onerror: ((event: { message: string }) => void) | null = null;
  postMessage = vi.fn();
  terminate = vi.fn();
  constructor() { FakeWorker.instances.push(this); }
  ready() { this.onmessage?.({ data: { type: 'ready', rapier: 'test', colReader: 'test' } }); }
}
beforeEach(() => {
  vi.useFakeTimers();
  FakeWorker.instances = [];
  vi.stubGlobal('Worker', FakeWorker);
  vi.stubGlobal('crossOriginIsolated', false);
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

test('a silent module startup is replaced once, preserving queued transfers and ignoring stale ready', async () => {
  const client = new PhysicsClient(), first = FakeWorker.instances[0];
  const lateMessage = first.onmessage!;
  const buffer = new ArrayBuffer(16);
  const loaded = client.loadTile('c_0_0', buffer);
  await vi.advanceTimersByTimeAsync(15_000);
  const second = FakeWorker.instances[1];
  expect(first.terminate).toHaveBeenCalledOnce();
  expect(first.onmessage).toBeNull();
  expect(first.postMessage).not.toHaveBeenCalled();
  lateMessage({ data: { type: 'ready', rapier: 'stale', colReader: 'stale' } });
  expect(second.postMessage).not.toHaveBeenCalled();
  second.ready();
  await expect(client.ready).resolves.toBe('test');
  expect(second.postMessage).toHaveBeenCalledExactlyOnceWith({ type: 'loadTile', id: 'c_0_0', buffer }, [buffer]);
  second.onmessage!({ data: { type: 'tileLoaded', stats: { id: 'c_0_0', tris: 1, prims: 0, bytes: 16, buildMs: 1, colliders: 1 } } });
  await expect(loaded).resolves.toMatchObject({ id: 'c_0_0' });
  await vi.advanceTimersByTimeAsync(60_000);
  expect(FakeWorker.instances).toHaveLength(2);
  expect(second.terminate).not.toHaveBeenCalled();
});

test('two silent starts reject readiness and queued loads instead of waiting forever', async () => {
  const client = new PhysicsClient();
  const ready = expect(client.ready).rejects.toThrow('two 15-second attempts');
  const loaded = expect(client.loadTile('c_0_0', new ArrayBuffer(0))).rejects.toThrow('two 15-second attempts');
  await vi.advanceTimersByTimeAsync(30_000);
  await ready; await loaded;
  expect(FakeWorker.instances).toHaveLength(2);
  expect(FakeWorker.instances.every(w => w.terminate.mock.calls.length === 1)).toBe(true);
  await vi.advanceTimersByTimeAsync(60_000);
  expect(FakeWorker.instances).toHaveLength(2);
});

test('a worker loading error rejects startup immediately', async () => {
  const client = new PhysicsClient();
  const ready = expect(client.ready).rejects.toThrow('module fetch failed');
  FakeWorker.instances[0].onerror!({ message: 'module fetch failed' });
  await ready;
  await vi.advanceTimersByTimeAsync(30_000);
  expect(FakeWorker.instances).toHaveLength(1);
});

test('a ready worker is never restarted by the startup watchdog', async () => {
  const client = new PhysicsClient(), worker = FakeWorker.instances[0];
  worker.ready(); await client.ready;
  await vi.advanceTimersByTimeAsync(60_000);
  expect(FakeWorker.instances).toHaveLength(1);
  expect(worker.terminate).not.toHaveBeenCalled();
});
