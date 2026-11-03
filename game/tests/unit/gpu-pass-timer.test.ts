import { expect, test, vi } from 'vitest';
import type * as THREE from 'three/webgpu';
import { GpuFrameTimer } from '../../src/render/gpuTimer';
import { gpuPassName, labelGpuPass } from '../../src/render/gpuPasses';

function gpu() {
  vi.stubGlobal('GPUBufferUsage', { QUERY_RESOLVE:1, COPY_SRC:2, COPY_DST:4, MAP_READ:8, STORAGE:16 });
  vi.stubGlobal('GPUMapMode', { READ:1 });
  let time=0n;
  const queries: any[]=[], writes: any[]=[], maps:(()=>void)[]=[], buffers:any[]=[];
  const pass=(descriptor:any,compute=false)=>{
    const w=descriptor.timestampWrites;
    if(!compute)writes.push(w);
    time+=1000000n;
    if(w?.beginningOfPassWriteIndex!==undefined)w.querySet.values[w.beginningOfPassWriteIndex]=time;
    return {setPipeline(){},setBindGroup(){},dispatchWorkgroups(){},end(){
      time+=1000000n;if(w?.endOfPassWriteIndex!==undefined)w.querySet.values[w.endOfPassWriteIndex]=time;
    }};
  };
  const device={
    features:new Set(['timestamp-query']),
    createQuerySet({count}:any){const q={values:new BigUint64Array(count),destroy:vi.fn()};queries.push(q);return q},
    createBuffer({size}:any){const b={bytes:new ArrayBuffer(size),mapAsync:()=>new Promise<void>(resolve=>maps.push(resolve)),
      getMappedRange(){return this.bytes},unmap:vi.fn(),destroy:vi.fn()};buffers.push(b);return b},
    createShaderModule(){return{}},createComputePipeline(){return{getBindGroupLayout(){return{}}}},createBindGroup(){return{}},
    queue:{submit:vi.fn(),onSubmittedWorkDone:async()=>{}},
    createCommandEncoder(){return{
      beginComputePass:(d:any)=>pass(d,true),beginRenderPass:(d:any)=>pass(d),finish:()=>({}),
      resolveQuerySet(q:any,start:number,count:number,b:any){new BigUint64Array(b.bytes).set(q.values.slice(start,start+count))},
      copyBufferToBuffer(a:any,offset:number,b:any,dst:number,size:number){new Uint8Array(b.bytes,dst,size).set(new Uint8Array(a.bytes,offset,size))},
    }},
  };
  const backend={isWebGPUBackend:true,trackTimestamp:true,device,beginRender(context:any){context.encoder=this.device.createCommandEncoder()}};
  const render=(context:any,count=1)=>{backend.beginRender(context);for(let i=0;i<count;i++)context.encoder.beginRenderPass({colorAttachments:[]}).end()};
  return {device,backend,render,queries,writes,maps,buffers,renderer:{backend} as unknown as THREE.WebGPURenderer};
}

test('actual layered passes get unique frame-owned queries; delayed readbacks never reuse a slot',async()=>{
  const g=gpu(), received:any[]=[];
  const original=g.device.createCommandEncoder,originalBegin=g.backend.beginRender;
  const timer=new GpuFrameTimer(g.renderer,(frame,ms,passes)=>received.push({frame,ms,passes}));
  try{
    const beauty={};labelGpuPass(beauty,'beauty');
    timer.begin(11);
    // Actual passes are opened after beginRender returns, as with array-camera layers.
    g.render({renderTarget:beauty,textures:[{name:'output'}]},2);
    g.render({renderTarget:{},textures:[{name:'ShadowMap'}]});
    timer.end();
    timer.begin(12);g.render({renderTarget:{},textures:[{name:'GTAONode.AO'}]});timer.end();
    expect(g.queries).toHaveLength(2);
    expect(g.writes.slice(0,3).map(w=>w.beginningOfPassWriteIndex)).toEqual([2,4,6]);
    expect(g.writes[0].querySet).not.toBe(g.writes[3].querySet);
    g.maps[1]();await Promise.resolve();g.maps[0]();await timer.flush();
    expect(received.map(r=>r.frame)).toEqual([12,11]);
    expect(received[0].passes).toEqual({ao:1});expect(received[1].passes).toEqual({beauty:2,shadows:1});
    expect(received[1].ms).toBeGreaterThan(3);
    g.render({renderTarget:beauty});expect(g.writes.at(-1)).toBeUndefined();
    await timer.dispose();
    expect(g.device.createCommandEncoder).toBe(original);expect(g.backend.beginRender).toBe(originalBegin);
    expect(g.queries.every(q=>q.destroy.mock.calls.length===1)).toBe(true);
  }finally{vi.unstubAllGlobals()}
});

test('overflow discards partial pass totals but retains whole-frame timing',async()=>{
  const g=gpu(),received:any[]=[];
  const timer=new GpuFrameTimer(g.renderer,(frame,ms,passes)=>received.push({frame,ms,passes}));
  try{
    timer.begin(1);g.render({renderTarget:{},textures:[{name:'UnrealBloomPass.h0'}]},129);timer.end();
    expect(g.writes.at(-1)).toBeUndefined();
    g.maps[0]();await timer.flush();
    expect(received[0].ms).toBeGreaterThan(0);expect(received[0].passes).toBeUndefined();
    expect(timer.passSkipped).toBe(1);await timer.dispose();
  }finally{vi.unstubAllGlobals()}
});

test('known effect targets classify separately while unknown offscreen work stays explicit',()=>{
  const target={};labelGpuPass(target,'prepass');
  expect(gpuPassName({renderTarget:target,textures:[{name:'output'}]})).toBe('prepass');
  for(const [name,label] of [['ShadowMap','shadows'],['TRAANode.resolve','traa'],['GTAONode.AO','ao'],
    ['UnrealBloomPass.v3','bloom'],['weather puddles 20+4 rgb-v3','puddles']])
    expect(gpuPassName({renderTarget:{},textures:[{name}]})).toBe(label);
  expect(gpuPassName({renderTarget:{},textures:[{name:''}]})).toBe('other');
  expect(gpuPassName({renderTarget:null,fullscreenPass:true})).toBe('post');
});
