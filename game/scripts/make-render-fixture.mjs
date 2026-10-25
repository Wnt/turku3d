// Tiny visual-only shader fixture, generated into the isolated build output; never shipped as an AOI layer.
import fs from 'node:fs/promises';
import path from 'node:path';
import { gameDist } from '../../tools/dev/environment.mjs';
import { Document, NodeIO } from '@gltf-transform/core';
import { BoxGeometry, PlaneGeometry } from 'three';

const out = process.argv[2] ?? path.join(gameDist, 'render-fixture');
const doc = new Document(), buffer = doc.createBuffer(), scene = doc.createScene();
function mesh(name, key, geometry, center, color) {
  const primitive = doc.createPrimitive();
  for (const [semantic, attribute] of [['POSITION', 'position'], ['NORMAL', 'normal'], ['TEXCOORD_0', 'uv']]) {
    const src = geometry.getAttribute(attribute);
    primitive.setAttribute(semantic, doc.createAccessor().setType(src.itemSize === 2 ? 'VEC2' : 'VEC3')
      .setArray(new Float32Array(src.array)).setBuffer(buffer));
  }
  primitive.setIndices(doc.createAccessor().setType('SCALAR').setArray(new Uint32Array(geometry.index.array)).setBuffer(buffer));
  primitive.setMaterial(doc.createMaterial(name).setBaseColorFactor([...color, 1]).setRoughnessFactor(0.85));
  scene.addChild(doc.createNode(name).setTranslation(center).setMesh(doc.createMesh(name).addPrimitive(primitive))
    .setExtras({ kind: key === 'water_river' ? 'water' : 'ground', id: name, material_key: key }));
}
mesh('asphalt', 'asphalt_standard', new BoxGeometry(12, 0.2, 14), [154, 2, 232], [0.15, 0.16, 0.18]);
mesh('curb', 'granite_curb', new BoxGeometry(0.3, 0.35, 14), [160.15, 2.075, 232], [0.55, 0.52, 0.5]);
mesh('cobbles', 'cobblestone_grey', new BoxGeometry(12, 0.2, 14), [166.3, 2, 232], [0.44, 0.41, 0.38]);
mesh('grass', 'grass_park', new BoxGeometry(12, 0.2, 14), [178.5, 2, 232], [0.18, 0.3, 0.09]);
mesh('river', 'water_river', new PlaneGeometry(40, 24).rotateX(-Math.PI / 2), [168, 2, 213], [0.1, 0.2, 0.2]);
mesh('facade', 'plaster_pale', new BoxGeometry(10, 14, 8), [172, 9, 194], [0.8, 0.35, 0.2]);
await fs.mkdir(out, { recursive: true });
await new NodeIO().write(path.join(out, 'fixture.glb'), doc);
await fs.writeFile(path.join(out, 'tileset.json'), JSON.stringify({ asset: { version: '1.0' }, geometricError: 0,
  root: { boundingVolume: { box: [168, -216, 9, 25, 0, 0, 0, 26, 0, 0, 0, 9] }, geometricError: 0,
    content: { uri: 'fixture.glb' }, extras: { id: 'render-fixture' } } }));
