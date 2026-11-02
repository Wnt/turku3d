import { expect, test, vi } from 'vitest';
import { ColFormatError } from '../../src/shared/col/colFormat';
import type { ToWorker } from '../../src/physics/protocol';

const mocks = vi.hoisted(() => ({ step:vi.fn(), loadTile:vi.fn(), raycast:vi.fn() }));
vi.mock('@dimforge/rapier3d', () => ({ version:()=>'test' }));
vi.mock('../../src/physics/world', () => ({ PhysicsWorld:class {
  step = mocks.step;
  loadTile = mocks.loadTile;
  raycast = mocks.raycast;
  characterState() { return null; }
  vehicleState() { return null; }
  vehicleStates() { return []; }
  poleStates() { return []; }
} }));

test('worker reports failed step sequence, continues its queue, and marks corrupt tiles permanent', async () => {
  const context = { postMessage:vi.fn(), onmessage:null as null | ((e:{data:ToWorker})=>void) };
  vi.stubGlobal('self', context);
  try {
    await import('../../src/physics/worker');
    mocks.step.mockImplementationOnce(()=>{throw new Error('step failed');}).mockReturnValue(1);
    mocks.raycast.mockReturnValueOnce({ toi: 1 }).mockReturnValueOnce(null);
    const ray = { origin: { x: 0, y: 2, z: 0 }, dir: { x: 0, y: -1, z: 0 }, maxToi: 3, filter: 'foot' as const };
    context.onmessage!({data:{type:'raycast',id:99,rays:[ray,{...ray,maxToi:0.5}]}});
    await vi.waitFor(()=>expect(context.postMessage).toHaveBeenCalledWith({type:'raycastResult',id:99,hits:[{toi:1},null]},[]));
    const step = {type:'step',seq:7,dt:1/60,character:{vx:0,vz:0,jump:false},vehicle:null} as const;
    context.onmessage!({data:step});
    context.onmessage!({data:{...step,seq:8}});
    mocks.loadTile.mockRejectedValue(new ColFormatError('CRC-32 mismatch'));
    context.onmessage!({data:{type:'loadTile',id:'c_0_0',buffer:new ArrayBuffer(4)}});
    await vi.waitFor(()=>expect(context.postMessage).toHaveBeenCalledWith(expect.objectContaining({type:'tileError',permanent:true}),[]));
    expect(context.postMessage).toHaveBeenCalledWith(expect.objectContaining({type:'stepError',seq:7,error:expect.stringContaining('step failed')}),[]);
    expect(context.postMessage).toHaveBeenCalledWith(expect.objectContaining({type:'state',seq:8}),[]);
  } finally { vi.unstubAllGlobals(); }
});
