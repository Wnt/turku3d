import { expect, test } from 'vitest';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { decodeGlb } from '../../src/world/decodeGlb';
import { parseDecodedGlb } from '../../src/world/loadGlb';

test('worker container decoding retains binary accessors, metadata and relative resource base', async () => {
  const json = { asset: { version: '2.0' }, buffers: [{ byteLength: 36 }], bufferViews: [{ buffer: 0, byteLength: 36 }],
    accessors: [{ bufferView: 0, componentType: 5126, count: 3, type: 'VEC3', min: [0, 0, 0], max: [1, 1, 0] }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 } }], extras: { building_id: 'retained' } }],
    nodes: [{ mesh: 0 }], scenes: [{ nodes: [0] }], scene: 0 };
  const text = new TextEncoder().encode(JSON.stringify(json)), size = Math.ceil(text.length / 4) * 4;
  const buffer = new ArrayBuffer(12 + 8 + size + 8 + 36), view = new DataView(buffer);
  view.setUint32(0, 0x46546c67, true); view.setUint32(4, 2, true); view.setUint32(8, buffer.byteLength, true);
  view.setUint32(12, size, true); view.setUint32(16, 0x4e4f534a, true);
  new Uint8Array(buffer, 20, size).fill(32); new Uint8Array(buffer, 20, text.length).set(text);
  view.setUint32(20 + size, 36, true); view.setUint32(24 + size, 0x004e4942, true);
  new Float32Array(buffer, 28 + size).set([0, 0, 0, 1, 0, 0, 0, 1, 0]);
  const decoded = decodeGlb(buffer);
  expect(decoded.json).toEqual(json);
  const loader = new GLTFLoader();
  const result = await parseDecodedGlb(loader, decoded, 'https://example.test/assets/');
  expect(result.parser.options.path).toBe('https://example.test/assets/');
  const mesh = result.scene.children[0] as import('three').Mesh;
  expect(mesh.userData.building_id).toBe('retained');
  expect([...mesh.geometry.attributes.position.array]).toEqual([0, 0, 0, 1, 0, 0, 0, 1, 0]);
  expect(() => decodeGlb(buffer.slice(0, -1))).toThrow('header');
});

test('bounded worker records preserve nested arrays, nulls and literal prototype metadata', async () => {
  const { jsonRecords, applyJsonRecords } = await import('../../src/world/glbRecords');
  const json = JSON.parse('{"meshes":[{"extras":{"__proto__":{"safe":true},"values":[1,null,"a",[]]}}],"asset":{"version":"2.0"}}');
  const objects: Record<string, unknown>[] = [{}], records = [...jsonRecords(json)];
  for (let i = 0; i < records.length; i += 3) applyJsonRecords(objects, records.slice(i, i + 3));
  expect(objects[0]).toEqual(json);
  expect(Object.getPrototypeOf(objects[0])).toBe(Object.prototype);
});
