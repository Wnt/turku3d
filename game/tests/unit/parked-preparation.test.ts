import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { ParkedCarVisuals } from '../../src/actors/car';
import { draftManifest } from '../../src/world/manifest';

test('all fleet LODs prepare before publication; expansion failure retains the resident draws', async () => {
  const scene = new THREE.Scene(), manifest = draftManifest('http://localhost/');
  const car = { object_id: 'one', model: 'fleet-sedan-0', color: 'blue', x: 1, y: 2, z: 3, yaw: 0,
    width: 2, length: 4, height: 1.5, clear_front_m: 10, clear_rear_m: 10 };
  const prepared: THREE.Object3D[]=[];
  let fail=false;
  const prepare=vi.fn(async(mesh:THREE.Object3D)=>{
    expect(mesh.parent).toBeNull();prepared.push(mesh);
    if(fail)throw new Error('GPU preparation failed');
  });
  const visuals=new ParkedCarVisuals(scene,prepare);
  manifest.vehicles=[car];await visuals.load(manifest);
  const old=[...scene.children];expect(old).toHaveLength(3);expect(prepare).toHaveBeenCalledTimes(3);
  manifest.vehicles.push({...car,object_id:'two'});await visuals.load(manifest);
  expect(scene.children).toEqual(old);expect(prepare).toHaveBeenCalledTimes(3);
  manifest.vehicles=Array.from({length:17},(_,i)=>({...car,object_id:String(i)}));fail=true;
  await expect(visuals.load(manifest)).rejects.toThrow('GPU preparation failed');
  expect(scene.children).toEqual(old);
  fail=false;await visuals.load(manifest);expect(scene.children).toHaveLength(3);
  expect(scene.children.every(m=>!old.includes(m))).toBe(true);expect(old.every(m=>!m.parent)).toBe(true);
});
