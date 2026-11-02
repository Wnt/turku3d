import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { SceneryBatches, sceneryBatchGroups } from '../../src/render/sceneryBatches';

test('bounds batches by expanded facade bytes and leaves an oversized source unmerged', () => {
  const sources = [0,1,2,3].map(() => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Int16BufferAttribute([0,0,0,1,0,0,0,1,0],3));
    geometry.setAttribute('_kit_opening', new THREE.Uint8BufferAttribute([1,2,3,4,5,6],2));
    return {mesh:new THREE.Mesh(geometry),mask:1};
  });
  // Each source expands to 36 position + 24 program bytes, despite only 24 source bytes.
  const groups = sceneryBatchGroups(sources,120);
  expect(groups.map(g=>g.length)).toEqual([2,2]);
  expect(groups.flat()).toEqual(sources);
  expect(sceneryBatchGroups(sources,59).map(g=>g.length)).toEqual([1,1,1,1]);
});

test('cell batching preserves world vertices and restores sources before a streaming handoff', () => {
  const scene = new THREE.Scene(), material = new THREE.MeshStandardNodeMaterial();
  const source = (x:number) => {
    const model = new THREE.Group(); model.position.x=x;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position',new THREE.Float32BufferAttribute([0,0,0,1,0,0,0,1,0],3));
    geometry.setAttribute('color',new THREE.Float32BufferAttribute([1,0,0,0,1,0,0,0,1],3));
    const mesh = new THREE.Mesh(geometry,material);mesh.userData.layer='buildings';model.add(mesh);scene.add(model);
    return {model,mesh};
  };
  const a=source(0),b=source(250),batches=new SceneryBatches(scene);
  const entries:[string,THREE.Object3D][]=[['t0_0_0',a.model],['t0_1_0',b.model]];
  batches.sync(entries);
  const merged=scene.children.find(c=>c.name==='distant-city-cell') as THREE.Mesh;
  expect(merged).toBeDefined();expect(a.mesh.layers.mask).toBe(0);expect(b.mesh.layers.mask).toBe(0);
  expect([...merged.geometry.attributes.position.array].sort((a,b)=>a-b)).toEqual([0,0,0,1,0,0,0,1,0,250,0,0,251,0,0,250,1,0].sort((a,b)=>a-b));
  expect(merged.geometry.attributes.color.count).toBe(6);
  b.model.visible=false;batches.sync(entries);
  expect(merged.parent).toBeNull();expect(a.mesh.layers.mask).toBe(1);expect(b.mesh.layers.mask).toBe(1);
  b.model.visible=true;batches.sync(entries);batches.clear();
  expect(a.mesh.layers.mask).toBe(1);expect(b.mesh.layers.mask).toBe(1);
  expect(scene.children.filter(c=>c.name==='distant-city-cell')).toHaveLength(0);
});

test('dequantizes meshopt positions before baking their world transform', () => {
  const scene = new THREE.Scene(), material = new THREE.MeshStandardNodeMaterial();
  const entries:[string,THREE.Object3D][] = [0,1].map(i=>{
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position',new THREE.Int16BufferAttribute([0,0,0,32767,0,0,0,32767,0],3,true));
    const mesh = new THREE.Mesh(geometry,material);mesh.userData.layer='ground';
    mesh.position.set(1000+i*250,20,-2000);mesh.scale.setScalar(250);scene.add(mesh);
    return [`t0_${i}_0`,mesh];
  });
  new SceneryBatches(scene).sync(entries);
  const merged=scene.children.find(c=>c.name==='distant-city-cell') as THREE.Mesh;
  expect(merged.geometry.attributes.position.array).toBeInstanceOf(Float32Array);
  expect(merged.geometry.boundingBox!.min.toArray()).toEqual([1000,20,-2000]);
  expect(merged.geometry.boundingBox!.max.toArray()).toEqual([1500,270,-2000]);
});
