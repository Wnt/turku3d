import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { interleaveProgram } from '../../src/render/facadeProgramAttributes';
import { mergeSceneryGeometry } from '../../src/render/mergeSceneryGeometry';

test('direct batch buffers retain transformed meshopt vertices, normals, tangents and facade instructions', () => {
  const meshes = [0,1].map(i => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Int16BufferAttribute([0,0,0,32767,0,0,0,32767,0],3,true));
    geometry.setAttribute('normal', new THREE.Float32BufferAttribute([0,0,1,0,0,1,0,0,1],3));
    geometry.setAttribute('tangent', new THREE.Float32BufferAttribute([1,0,0,-1,1,0,0,-1,1,0,0,-1],4));
    geometry.setAttribute('color', new THREE.Uint8BufferAttribute([255,128,0,0,255,128,128,0,255],3,true));
    geometry.setAttribute('uv', new THREE.Uint16BufferAttribute([0,0,65535,0,0,65535],2,true));
    geometry.setAttribute('_kit_opening', new THREE.Float32BufferAttribute([1,2,3,4,1,2,3,4,1,2,3,4],4));
    geometry.setAttribute('_kit_wall', new THREE.Uint8BufferAttribute([30,60,90,30,60,90,30,60,90],3,true));
    geometry.setIndex([0,1,2]);
    interleaveProgram(geometry);
    const mesh = new THREE.Mesh(geometry);
    mesh.position.set(125 + i * 250,17,-40); mesh.scale.set(250,100,50);
    mesh.rotation.set(.3,.8,-.4); mesh.updateMatrixWorld();
    return mesh;
  });
  // Reference the previous bake contract, including normalized accessor expansion.
  const baked = meshes.map(mesh => {
    const geometry = new THREE.BufferGeometry();
    for (const [name,attribute] of Object.entries(mesh.geometry.attributes)) {
      const values = Float32Array.from({length:attribute.count*attribute.itemSize},
        (_,i)=>attribute.getComponent(Math.floor(i/attribute.itemSize),i%attribute.itemSize));
      geometry.setAttribute(name,new THREE.BufferAttribute(values,attribute.itemSize));
    }
    geometry.setIndex(mesh.geometry.index!.clone());
    return geometry.applyMatrix4(mesh.matrixWorld);
  });
  const expected = mergeGeometries(baked)!;
  const originalPositions = meshes.map(mesh => mesh.geometry.attributes.position.array.slice());
  for (const mesh of meshes) mesh.geometry.clone = () => { throw new Error('source clone allocates another entire facade'); };
  const actual = mergeSceneryGeometry(meshes);
  expect([...actual.index!.array]).toEqual([...expected.index!.array]);
  for (const [name,attribute] of Object.entries(expected.attributes)) {
    const result = actual.attributes[name];
    expect(result.count).toBe(attribute.count);
    for (let i=0;i<attribute.count;i++) for (let c=0;c<attribute.itemSize;c++)
      expect(result.getComponent(i,c)).toBeCloseTo(attribute.getComponent(i,c),4);
  }
  const opening = actual.attributes._kit_opening as THREE.InterleavedBufferAttribute;
  const wall = actual.attributes._kit_wall as THREE.InterleavedBufferAttribute;
  expect(opening.data).toBe(wall.data);
  expect(opening.data.array.length).toBe(6*7);
  expect(actual.attributes.color.array).toBeInstanceOf(Uint8Array);
  expect(actual.attributes.uv.array).toBeInstanceOf(Uint16Array);
  for (const [i,mesh] of meshes.entries()) expect(mesh.geometry.attributes.position.array).toEqual(originalPositions[i]);
  const position = actual.attributes.position;
  if (!(position instanceof THREE.BufferAttribute)) throw Error('Merged positions must be a BufferAttribute');
  expect(actual.boundingBox).toEqual(new THREE.Box3().setFromBufferAttribute(position));
  actual.dispose(); expected.dispose();
  for (const geometry of baked) geometry.dispose();
  for (const mesh of meshes) { mesh.geometry.dispose(); (mesh.material as THREE.Material).dispose(); }
});

test('unindexed scenery retains its triangle order without allocating an index', () => {
  const a = new THREE.Mesh(new THREE.BoxGeometry().toNonIndexed());
  const b = new THREE.Mesh(a.geometry); b.position.x = 250; b.updateMatrixWorld();
  const merged = mergeSceneryGeometry([a,b]);
  expect(merged.index).toBeNull();
  expect(merged.attributes.position.count).toBe(a.geometry.attributes.position.count * 2);
  expect(merged.boundingBox!.min.x).toBe(-.5);
  expect(merged.boundingBox!.max.x).toBe(250.5);
  merged.dispose(); a.geometry.dispose();
});
