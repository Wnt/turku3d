import { test, expect, vi } from 'vitest';
import { PhysicsClient } from '../../src/physics/client';
import type { FromWorker } from '../../src/physics/protocol';

test('explicit steps do not depend on replies and worker errors are surfaced', async () => {
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
    expect(worker.postMessage).toHaveBeenCalledTimes(2);
    worker.onmessage!({ data:{type:'stepError',seq:1,error:'test step failure'} });
    expect(errors).toHaveBeenCalledWith('step 1: test step failure');
    client.step(1/60,{vx:0,vz:0,jump:false},null);
    expect(worker.postMessage).toHaveBeenCalledTimes(3);
    expect(worker.postMessage.mock.calls[2][0]).toMatchObject({type:'step',seq:3,dt:1/60});
    worker.onmessage!({ data: { type: 'tileLoaded', stats: { id: 'c_2_3', tris: 1, prims: 0, bytes: 4, buildMs: 1, colliders: 1 } } });
    expect(client.loaded.has('c_2_3')).toBe(true);
    worker.onmessage!({ data: { type: 'tileError', id: 'c_2_3', error: 'decode failed', permanent: true } });
    expect(client.loaded.has('c_2_3')).toBe(false);
  } finally { vi.unstubAllGlobals(); }
});

test('live fallback input keeps flowing without state replies, including pause', async () => {
  class FakeWorker {
    static instance: FakeWorker;
    onmessage?: (e: { data: FromWorker }) => void;
    postMessage = vi.fn();
    constructor() { FakeWorker.instance = this; }
  }
  vi.stubGlobal('Worker', FakeWorker);
  vi.stubGlobal('crossOriginIsolated', false);
  try {
    const client = new PhysicsClient(), worker = FakeWorker.instance;
    worker.onmessage!({ data: { type: 'ready', rapier: 'test', colReader: 'test' } });
    await client.ready;
    client.updateInput({ vx: 1, vz: 0, jump: true }, null, true);
    client.updateInput({ vx: 2, vz: 0, jump: false }, null, true);
    client.updateInput({ vx: 0, vz: 0, jump: false }, null, false);
    expect(worker.postMessage.mock.calls.map(c => c[0])).toMatchObject([
      { type: 'input', input: { jump: 1, running: true, character: { vx: 1 } } },
      { type: 'input', input: { jump: 1, running: true, character: { vx: 2 } } },
      { type: 'input', input: { jump: 1, running: false } },
    ]);
  } finally { vi.unstubAllGlobals(); }
});

test('shared latest is read synchronously without any worker state message', async () => {
  const { SharedMailbox } = await import('../../src/physics/exchange');
  class FakeWorker {
    static instance: FakeWorker;
    onmessage?: (e: { data: FromWorker }) => void;
    postMessage = vi.fn();
    constructor() { FakeWorker.instance = this; }
  }
  vi.stubGlobal('Worker', FakeWorker);
  vi.stubGlobal('crossOriginIsolated', true);
  try {
    const client = new PhysicsClient(), worker = FakeWorker.instance;
    worker.onmessage!({ data: { type: 'ready', rapier: 'test', colReader: 'test' } });
    await client.ready;
    const exchange = worker.postMessage.mock.calls[0][0];
    const state = new SharedMailbox<FromWorker>(exchange.state);
    const input = new SharedMailbox<{ jump: number; running: boolean }>(exchange.input);
    client.updateInput({ vx: 0, vz: 0, jump: true }, null, true);
    client.updateInput({ vx: 1, vz: 0, jump: false }, null, true);
    expect(input.read()).toMatchObject({ jump: 1, running: true });
    const character = { pos: { x: 4, y: 1, z: 2 }, vel: { x: 1, y: 0, z: 0 }, grounded: true, enabled: true, groundMaterial: 0 };
    state.write({ type: 'state', seq: 1, tick: 120, substeps: 1, stepMs: 2, remainder: 0,
      character, previousCharacter: character, vehicle: null, previousVehicle: null, vehicles: [], poles: [] });
    expect(client.latest?.character?.pos.x).toBe(4);
    expect(client.characterRender.sample(performance.now() / 1000)?.pos.x).toBe(4);
    expect(worker.postMessage).toHaveBeenCalledTimes(1);
  } finally { vi.unstubAllGlobals(); }
});


test('same-turn foot probes share one worker snapshot and retain result order and input values', async () => {
  class FakeWorker {
    static instance: FakeWorker;
    onmessage?: (e: { data: FromWorker }) => void;
    postMessage = vi.fn();
    constructor() { FakeWorker.instance = this; }
  }
  vi.stubGlobal('Worker', FakeWorker); vi.stubGlobal('crossOriginIsolated', false);
  try {
    const client = new PhysicsClient(), worker = FakeWorker.instance;
    worker.onmessage!({ data: { type: 'ready', rapier: 'test', colReader: 'test' } });
    await client.ready;
    const origin = { x: 1, y: 2, z: 3 }, direction = { x: 0, y: -1, z: 0 };
    const results = Array.from({ length: 24 }, (_, i) => { origin.x = i; return client.raycast(origin, direction, 1.3, 'foot'); });
    origin.x = 999; direction.y = 1;
    await Promise.resolve();
    expect(worker.postMessage).toHaveBeenCalledTimes(1);
    const message = worker.postMessage.mock.calls[0][0];
    expect(message.rays.map((r: any) => r.origin.x)).toEqual(Array.from({ length: 24 }, (_, i) => i));
    expect(message.rays.every((r: any) => r.dir.y === -1 && r.filter === 'foot')).toBe(true);
    const hits = message.rays.map((r: any, i: number) => i % 2 ? null : { toi: 1, point: r.origin,
      normal: { x: 0, y: 1, z: 0 }, material: 17, tile: 'c_0_0' });
    worker.onmessage!({ data: { type: 'raycastResult', id: message.id, hits } });
    expect(await Promise.all(results)).toEqual(hits);
    const later = client.raycast({ x: 25, y: 2, z: 0 }, { x: 0, y: -1, z: 0 }, 1);
    await Promise.resolve();
    expect(worker.postMessage).toHaveBeenCalledTimes(2);
    const second = worker.postMessage.mock.calls[1][0];
    worker.onmessage!({ data: { type: 'raycastResult', id: second.id, hits: [null] } });
    expect(await later).toBeNull();
  } finally { vi.unstubAllGlobals(); }
});
