import assert from 'node:assert/strict';
import { test } from 'node:test';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { batchVisual } from './batch.mjs';

const require = createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS ?? path.join(os.homedir(), 'turku3d/tools/npm'), 'package.json'));
const { Document, NodeIO } = require('@gltf-transform/core');
const functions = require('@gltf-transform/functions');

test('river reflection eligibility survives material deduplication and GLB batching', async () => {
  const doc = new Document(), buffer = doc.createBuffer(), scene = doc.createScene();
  const material = doc.createMaterial().setBaseColorFactor([1, 1, 1, 1]);
  for (const [i, id] of ['hull', 'cabin', 'rails', 'furniture'].entries()) {
    const positions = doc.createAccessor().setBuffer(buffer).setType('VEC3')
      .setArray(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]));
    const prim = doc.createPrimitive().setAttribute('POSITION', positions).setMaterial(material);
    scene.addChild(doc.createNode().setTranslation([i * 2, 0, 0])
      .setExtras({ layer: 'boats', material_key: 'boat_white', object_id: id, reflection_surface: i < 2 })
      .setMesh(doc.createMesh().addPrimitive(prim)));
  }
  await batchVisual(doc, scene, 't0_0_0', functions);
  const io = new NodeIO(), decoded = await io.readBinary(await io.writeBinary(doc));
  const nodes = decoded.getRoot().listNodes().filter(node => node.getMesh());
  assert.equal(nodes.length, 2);
  for (const node of nodes) {
    const extras = node.getExtras();
    const expected = extras.reflection_surface ? ['cabin', 'hull'] : ['furniture', 'rails'];
    assert.deepEqual(extras.objects.map(object => object.object_id).sort(), expected);
    assert.ok(extras.objects.every(object => object.reflection_surface === extras.reflection_surface));
    assert.equal(node.getMesh().listPrimitives()[0].getAttribute('POSITION').getCount(), 6);
  }
});
