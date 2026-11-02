import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { CoarseTiles } from '../../src/world/coarseTiles';
import type { CoarseFragment, CoarseHost, Entry } from '../../src/world/coarseContracts';
import { cellsOf } from '../../src/world/coarseRegion';

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
