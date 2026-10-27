import { test, expect, vi } from 'vitest';
import { PhysicsClient } from '../../src/physics/client';
import type { FromWorker } from '../../src/physics/protocol';

test('a failed worker step releases the in-flight slot and surfaces the error', async () => {
  class FakeWorker {
    static instance: FakeWorker;
    onmessage?: (e: { data: FromWorker }) => void;
    onerror?: (e: { message: string }) => void;
    postMessage = vi.fn();
    constructor() { FakeWorker.instance = this; }
  }
  vi.stubGlobal('Worker', FakeWorker);
  try {
    const client = new PhysicsClient(); const worker = FakeWorker.instance;
    const errors = vi.fn(); client.onError = errors;
    worker.onmessage!({ data:{type:'ready', rapier:'test', colReader:'test'} });
    await client.ready;
    client.step(1/60,{vx:0,vz:0,jump:false},null);
    client.step(1/60,{vx:0,vz:0,jump:false},null);
    expect(worker.postMessage).toHaveBeenCalledTimes(1);
    worker.onmessage!({ data:{type:'stepError',seq:1,error:'test step failure'} });
    expect(errors).toHaveBeenCalledWith('step 1: test step failure');
    client.step(1/60,{vx:0,vz:0,jump:false},null);
    expect(worker.postMessage).toHaveBeenCalledTimes(2);
    expect(worker.postMessage.mock.calls[1][0]).toMatchObject({type:'step',seq:2,dt:1/60});
  } finally { vi.unstubAllGlobals(); }
});
