import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { CoarseTiles } from '../../src/world/coarseTiles';
import type { CoarseFragment, CoarseHost, Entry } from '../../src/world/coarseContracts';
import { cellsOf } from '../../src/world/coarseRegion';
import { VisualPassComparison, type ComparisonEntry } from '../../src/world/visualPass';

test('full preparation keeps the first pass resident, commits once, and ignores a later first fragment', async () => {
  const scene = new THREE.Scene(), first = new THREE.Group(), full = new THREE.Group(); scene.add(first);
  let prepared!: () => void;
  const host = { scene, pageUrl: 'http://localhost:5910/', renderer: {}, manifest: { collision: {} },
    refreshCollision() {}, unloadCollision() {}, checkCollision: async () => {},
    loadModel: async () => full, styled: () => new Promise<void>(resolve => { prepared = resolve; }) } as unknown as CoarseHost;
  const coarse = new CoarseTiles(host);
  const collision = Object.fromEntries(cellsOf('t0_0_0').map(id => [id, { url: 'col', sha256: 'col', bytes: 0, tris:0, h:[0,1] as [number,number] }]));
  const entry: Entry = { state:'draft', pass:'first', model:first, visualHash:'first', collision:new Map(Object.entries(collision)), nextPoll:0 };
  coarse.entries.set('t0_0_0', entry);
  const fragment: CoarseFragment = { coarse:true, tile:'t0_0_0', pass_kind:'full', collision, visual:{t0_0_0:{url:'full.glb',sha256:'full',bytes:0,tris:0,h:[0,1]}} };
  const apply = (f: CoarseFragment) => (coarse as unknown as { applyFragment(id:string,e:Entry,f:CoarseFragment):Promise<void> }).applyFragment('t0_0_0',entry,f);
  const pending = apply(fragment);
  for (let i=0;i<20&&!prepared;i++) await Promise.resolve();
  expect(prepared).toBeTypeOf('function');
  expect(scene.children).toEqual([first]); expect(entry.pass).toBe('first');
  prepared(); await pending;
  expect(scene.children).toEqual([full]); expect(entry.pass).toBe('full');
  await apply({...fragment,pass_kind:'first',visual:{t0_0_0:{...fragment.visual.t0_0_0,sha256:'later-first'}}});
  expect(scene.children).toEqual([full]); expect(entry.visualHash).toBe('full');
});

function returnCache(count=1,detailRadius?:number) {
  const scene=new THREE.Scene(),loadModel=vi.fn(async()=>new THREE.Group()),untrackModel=vi.fn();
  const coarse=new CoarseTiles({scene,renderer:{} as THREE.WebGPURenderer,pageUrl:'http://localhost:5910/',
    manifest:{collision:{}} as CoarseHost['manifest'],detailRadius,loadModel,untrackModel,
    checkCollision:async()=>{},refreshCollision(){},unloadCollision(){},
    fetch:async()=>new Response('{"state":"queued"}',{status:202})});
  for(let i=0;i<count;i++) {
    const model=new THREE.Group();scene.add(model);
    coarse.entries.set(`t0_${i}_0`,{state:'draft',pass:'full',visualHash:`full:${i}`,model,resident:true,nextPoll:Infinity,collision:new Map()});
  }
  return {coarse,scene,loadModel,untrackModel};
}

test('a quick reversal reattaches the same GPU-ready full visual before the next world scan', () => {
  const {coarse,scene,loadModel}=returnCache();
  const entry=coarse.entries.get('t0_0_0')!,model=entry.model;
  coarse.update(10000,10000,1000);
  expect(entry.cachedAt).toBe(1000);expect(model!.parent).toBeNull();expect(model!.visible).toBe(false);
  coarse.update(125,125,1001);
  expect(entry.model).toBe(model);expect(entry.pass).toBe('full');expect(entry.visualHash).toBe('full:0');
  expect(entry.cachedAt).toBeUndefined();expect(model!.parent).toBe(scene);expect(model!.visible).toBe(true);
  expect(loadModel).not.toHaveBeenCalled();
});

test('the return cache bounds GPU residency and expires; mobile still releases full models immediately', () => {
  const {coarse,untrackModel}=returnCache(20);
  coarse.update(10000,10000,1000);
  expect([...coarse.entries.values()].filter(e=>e.cachedAt!==undefined)).toHaveLength(16);
  expect(untrackModel).toHaveBeenCalledTimes(4);
  coarse.update(10000,10000,32001);
  expect([...coarse.entries.values()].filter(e=>e.cachedAt!==undefined)).toHaveLength(0);
  expect(untrackModel).toHaveBeenCalledTimes(20);
  const mobile=returnCache(1,80);mobile.coarse.update(10000,10000,1000);
  expect(mobile.coarse.entries.has('t0_0_0')).toBe(false);
  expect(mobile.untrackModel).toHaveBeenCalledTimes(1);
});

test('a refresh completing during cached residency stays detached until re-entry', async () => {
  const {coarse,scene,loadModel}=returnCache(),entry=coarse.entries.get('t0_0_0')!;
  const collision=Object.fromEntries(cellsOf('t0_0_0').map(id=>[id,{url:'col',sha256:'same',bytes:0,tris:0,h:[0,1] as [number,number]}]));
  entry.collision=new Map(Object.entries(collision));
  coarse.update(10000,10000,1000);
  const fragment:CoarseFragment={coarse:true,tile:'t0_0_0',pass_kind:'full',collision,
    visual:{t0_0_0:{url:'new.glb',sha256:'new',bytes:0,tris:0,h:[0,1]}}};
  await (coarse as unknown as {applyFragment(id:string,e:Entry,f:CoarseFragment):Promise<void>}).applyFragment('t0_0_0',entry,fragment);
  expect(loadModel).toHaveBeenCalledTimes(1);expect(entry.model!.parent).toBeNull();expect(entry.model!.visible).toBe(false);
  expect(entry.visualHash).toBe('new');
  coarse.update(125,125,1001);
  expect(entry.model!.parent).toBe(scene);expect(entry.model!.visible).toBe(true);
});

test('a delayed comparison cannot attach a first visual after the full model enters the return cache', async () => {
  const scene=new THREE.Scene(),base=new THREE.Group(),first=new THREE.Group();scene.add(base);
  const entry:ComparisonEntry={model:base,pass:'full'};
  const entries=new Map([['t0_0_0',entry]]),dispose=vi.fn();
  let finish!:(model:THREE.Object3D)=>void;
  const comparison=new VisualPassComparison({scene} as CoarseHost,entries,
    async()=>new Response('{"pass_kind":"first","visual":{"t0_0_0":{"url":"first.glb"}}}'),
    ()=>new Promise(resolve=>{finish=resolve;}),()=>{},dispose,'/coarse/','http://localhost:5910/');
  comparison.setPass('first');const pending=comparison.compare('t0_0_0',entry);
  await vi.waitFor(()=>expect(finish).toBeTypeOf('function'));
  entry.cachedAt=1000;base.removeFromParent();finish(first);await pending;
  expect(scene.children).toEqual([]);expect(first.parent).toBeNull();expect(dispose).toHaveBeenCalledWith(first);
});
