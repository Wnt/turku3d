import { expect, test, vi } from 'vitest';
import { FileLoader } from 'three/webgpu';
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


test('external shared buffers are fetched once across separate glTF parsers', async () => {
  const binary = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]).buffer;
  const load = vi.spyOn(FileLoader.prototype, 'load').mockImplementation((_url, done) => { queueMicrotask(() => done?.(binary)); });
  const json = { asset: { version: '2.0' }, buffers: [{ byteLength: 36, uri: `/coarse/shared/${'b'.repeat(64)}.bin` }],
    bufferViews: [{ buffer: 0, byteLength: 36 }],
    accessors: [{ bufferView: 0, componentType: 5126, count: 3, type: 'VEC3', min: [0, 0, 0], max: [1, 1, 0] }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 } }] }], nodes: [{ mesh: 0 }], scenes: [{ nodes: [0] }], scene: 0 };
  try {
    const results = await Promise.all(Array.from({ length: 3 }, () =>
      parseDecodedGlb(new GLTFLoader(), { json: structuredClone(json), binary: null }, 'https://example.test/coarse/t0_0_0/')));
    expect(load).toHaveBeenCalledTimes(1);
    for (const result of results) {
      const mesh = result.scene.children[0] as import('three').Mesh;
      expect([...mesh.geometry.attributes.position.array]).toEqual([...new Float32Array(binary)]);
    }
  } finally { load.mockRestore(); }
});

test('a paced parse (accessors and meshes wait for the streaming window) still builds every mesh of a big tile', async () => {
  const meshes = 3000, floats = new Float32Array(meshes * 9);
  for (let i = 0; i < meshes; i++) floats.set([i, 0, 0, i + 1, 0, 0, i, 1, 0], i * 9);
  const json = { asset: { version: '2.0' }, buffers: [{ byteLength: floats.byteLength }],
    bufferViews: Array.from({ length: meshes }, (_, i) => ({ buffer: 0, byteOffset: i * 36, byteLength: 36 })),
    accessors: Array.from({ length: meshes }, (_, i) => ({ bufferView: i, componentType: 5126, count: 3, type: 'VEC3', min: [i, 0, 0], max: [i + 1, 1, 0] })),
    meshes: Array.from({ length: meshes }, (_, i) => ({ primitives: [{ attributes: { POSITION: i } }], extras: { building_id: `b${i}` } })),
    nodes: Array.from({ length: meshes }, (_, i) => ({ mesh: i })), scenes: [{ nodes: Array.from({ length: meshes }, (_, i) => i) }], scene: 0 };
  // Frames every 4 ms: the streaming window fills and the parse continues over several of them.
  let frames = 0, late = 0;
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    const due = performance.now() + 4;
    setTimeout(() => { frames++; late = Math.max(late, performance.now() - due); callback(performance.now()); }, 4);
  });
  let result;
  try { result = await parseDecodedGlb(new GLTFLoader(), { json, binary: floats.buffer.slice(0) }, 'https://example.test/coarse/t0_0_0/'); }
  finally { vi.unstubAllGlobals(); }
  process.stderr.write(`paced parse: ${frames} frames, longest block ${late.toFixed(1)} ms\n`);
  const built = result.scene.children as import('three').Mesh[];
  expect(frames).toBeGreaterThan(2);
  expect(built.length).toBe(meshes);
  for (const [i, mesh] of built.entries()) {
    expect(mesh.userData.building_id).toBe(`b${i}`);
    expect(mesh.geometry.attributes.position.getX(1)).toBe(i + 1);
  }
});
