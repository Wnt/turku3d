import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { interleaveProgram } from '../../src/render/facadeProgramAttributes';
import { mergeSceneryGeometry } from '../../src/render/mergeSceneryGeometry';
import { shareSceneryProgram } from '../../src/render/shareSceneryProgram';
import { SceneryBatches } from '../../src/render/sceneryBatches';

function source(value: number, material = new THREE.MeshStandardNodeMaterial()) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([0,0,0,1,0,0,0,1,0],3));
  geometry.setAttribute('_kit_opening', new THREE.Float32BufferAttribute([value,2,3,4,value,2,3,4,value,2,3,4],4));
  geometry.setAttribute('_kit_wall', new THREE.Float32BufferAttribute([.1,.2,.3,.1,.2,.3,.1,.2,.3],3));
  interleaveProgram(geometry);
  const mesh = new THREE.Mesh(geometry, material); mesh.userData.layer = 'buildings-program';
  return mesh;
}
const program = (mesh: THREE.Mesh) => (mesh.geometry.attributes._kit_opening as THREE.InterleavedBufferAttribute).data;
const bytes = (buffers: THREE.InterleavedBuffer[]) => [...new Set(buffers.map(b => b.array.buffer))]
  .reduce((sum, buffer) => sum + buffer.byteLength, 0);

test('batch shares immutable CPU bytes without changing source GPU attribute owners or values', () => {
  const scene = new THREE.Scene(), a = source(11), b = source(17, a.material);
  scene.add(a,b);
  const owners = [program(a),program(b)], values = owners.map(b=>b.array.slice());
  const attributes = [a.geometry.attributes._kit_opening,b.geometry.attributes._kit_opening];
  const batches = new SceneryBatches(scene);
  batches.sync([['t0_0_0',a],['t0_1_0',b]],0);
  const merged = scene.children.find(m=>m.name==='distant-city-cell') as THREE.Mesh;
  const cell = program(merged);
  expect(bytes([...owners,cell])).toBe(values.reduce((sum,a)=>sum+a.byteLength,0));
  for (const [i,mesh] of [a,b].entries()) {
    expect(program(mesh)).toBe(owners[i]); expect(program(mesh)).not.toBe(cell);
    expect(mesh.geometry.attributes._kit_opening).toBe(attributes[i]);
    expect(owners[i].array.buffer).toBe(cell.array.buffer);
    expect(owners[i].array).toEqual(values[i]); expect(owners[i].count).toBe(3);
    expect(owners[i].version).toBe(0); expect(mesh.layers.mask).toBe(0);
  }
  // Removing a neighbour restores the surviving source without retaining its old cell.
  batches.sync([['t0_0_0',a]],10);
  for (const [i,mesh] of [a,b].entries()) {
    expect(mesh.layers.mask).toBe(1); expect(owners[i].array).toEqual(values[i]);
    expect(owners[i].array.buffer).not.toBe(cell.array.buffer);
    expect(owners[i].array.buffer.byteLength).toBe(values[i].byteLength);
  }
  batches.sync([['t0_0_0',a],['t0_1_0',b]],20); batches.sync([['t0_0_0',a],['t0_1_0',b]],120);
  batches.clear();
  expect(owners[0].array.buffer).not.toBe(owners[1].array.buffer);
  expect(owners[0].array).toEqual(values[0]); expect(owners[1].array).toEqual(values[1]);
});

test('dynamic and differently ordered source layouts retain their independent storage', () => {
  const a=source(11), b=source(17);
  const first=program(a), second=program(b);
  first.setUsage(THREE.DynamicDrawUsage);
  // A valid reversed layout needs conversion in the merge, so it cannot share raw slices.
  const geometry=b.geometry;
  geometry.setAttribute('_kit_wall',new THREE.InterleavedBufferAttribute(second,3,0));
  geometry.setAttribute('_kit_opening',new THREE.InterleavedBufferAttribute(second,4,3));
  const original=[first.array,second.array], merged=mergeSceneryGeometry([a,b]);
  const release=shareSceneryProgram([a,b],merged);
  expect(first.array).toBe(original[0]); expect(second.array).toBe(original[1]);
  release(); expect(first.array).toBe(original[0]); expect(second.array).toBe(original[1]);
});

test('release is idempotent and does not overwrite replacement source storage', () => {
  const a=source(11), b=source(17), merged=mergeSceneryGeometry([a,b]);
  const release=shareSceneryProgram([a,b],merged);
  const replacement=program(a).array.slice(); program(a).array=replacement;
  release(); const detached=program(b).array;
  release(); expect(program(a).array).toBe(replacement); expect(program(b).array).toBe(detached);
});
