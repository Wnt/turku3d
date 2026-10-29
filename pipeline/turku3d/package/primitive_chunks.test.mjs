import assert from 'node:assert/strict';
import { test } from 'node:test';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { batchVisual } from './batch.mjs';
import { MAX_BATCH_INDICES, primitiveChunks } from './primitive_chunks.mjs';

const require = createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS ?? path.join(os.homedir(), 'turku3d/tools/npm'), 'package.json'));
const { Document, NodeIO } = require('@gltf-transform/core');
const { EXTMeshoptCompression } = require('@gltf-transform/extensions');
const { MeshoptEncoder, MeshoptDecoder } = require('meshoptimizer');
const functions = require('@gltf-transform/functions');

function fixture(count, indexed = true) {
  const doc = new Document(), buffer = doc.createBuffer(), scene = doc.createScene();
  const attribute = (type, array) => doc.createAccessor().setBuffer(buffer).setType(type).setArray(array);
  const positions = Float32Array.from({ length:count * 3 }, (_, i) => Math.fround(Math.sin(i) * 250));
  const program = Float32Array.from({ length:count * 4 }, (_, i) => Math.floor(i / 12));
  const prim = doc.createPrimitive().setAttribute('POSITION', attribute('VEC3', positions))
    .setAttribute('_KIT_EXTENT', attribute('VEC4', program)).setMaterial(doc.createMaterial());
  if (indexed) prim.setIndices(attribute('SCALAR', Uint32Array.from({ length:count }, (_, i) => i)));
  scene.addChild(doc.createNode().setExtras({ layer:'buildings', object_id:'dense-wall', building_id:'house',
    facade_kit_walls:[{ start:0, count, source:'already compiled' }] })
    .setMesh(doc.createMesh().addPrimitive(prim)));
  return { doc, scene, prim, positions, program };
}

for (const indexed of [true, false]) test(`triangle chunks preserve all attributes (indexed=${indexed})`, () => {
  const { prim, positions, program } = fixture(15, indexed);
  const parts = primitiveChunks(prim, functions.compactPrimitive, 7);
  assert.deepEqual(parts.map(p => p.getIndices().getCount()), [6, 6, 3]);
  for (const [semantic, expected] of [['POSITION', positions], ['_KIT_EXTENT', program]]) {
    const actual = parts.flatMap(p => Array.from(p.getAttribute(semantic).getArray()));
    assert.deepEqual(actual, Array.from(expected));
  }
  assert.equal(prim.getAttribute('POSITION').getCount(), 15);
});

test('dense batches round-trip through meshopt within payload limits and discard only compiled source rows', async () => {
  await Promise.all([MeshoptEncoder.ready, MeshoptDecoder.ready]);
  const { doc, scene, positions, program } = fixture(MAX_BATCH_INDICES + 6);
  await batchVisual(doc, scene, 't0_0_0', functions);
  const nodes = scene.listChildren().filter(n => n.getMesh());
  assert.equal(nodes.length, 2);
  for (const node of nodes) {
    assert.equal(node.getExtras().building_id, 'house');
    assert.equal(node.getExtras().facade_kit_walls, undefined);
  }
  await doc.transform(functions.prune({ keepAttributes:true }), functions.unpartition());
  doc.createExtension(EXTMeshoptCompression).setRequired(true)
    .setEncoderOptions({ method:EXTMeshoptCompression.EncoderMethod.QUANTIZE });
  const io = new NodeIO().registerExtensions([EXTMeshoptCompression])
    .registerDependencies({ 'meshopt.encoder':MeshoptEncoder, 'meshopt.decoder':MeshoptDecoder });
  const binary = await io.writeBinary(doc);
  const json = JSON.parse(Buffer.from(binary).subarray(20, 20 + new DataView(binary.buffer).getUint32(12, true)));
  for (const view of json.bufferViews) assert.ok(view.extensions.EXT_meshopt_compression.byteLength < 4_000_000);
  const decoded = await io.readBinary(binary);
  const primitives = decoded.getRoot().listMeshes().flatMap(m => m.listPrimitives());
  for (const [semantic, expected] of [['POSITION', positions], ['_KIT_EXTENT', program]]) {
    const actual = primitives.flatMap(p => Array.from(p.getAttribute(semantic).getArray()));
    assert.deepEqual(actual, Array.from(expected));
  }
});
