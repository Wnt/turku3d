import assert from 'node:assert/strict';
import { test } from 'node:test';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { auditVisual } from './visual_audit.mjs';
import { bindProgram } from './facade_program.mjs';
const require = createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS ?? path.join(os.homedir(),'turku3d/tools/npm'),'package.json'));
const { Document } = require('@gltf-transform/core');
const { EXTMeshGPUInstancing } = require('@gltf-transform/extensions');
function fixture() {
  const doc = new Document(), scene=doc.createScene(), buffer=doc.createBuffer();
  const acc=(type,array)=>doc.createAccessor().setType(type).setArray(array).setBuffer(buffer);
  const primitive=doc.createPrimitive().setAttribute('POSITION',acc('VEC3',new Float32Array([0,0,0, 0,2,0, 2,2,0])))
    .setIndices(acc('SCALAR',new Uint32Array([0,1,2])));
  return {doc,scene,primitive,acc};
}
test('shipped audit includes props, vegetation instances and complete node transforms', () => {
  const {doc,scene,primitive,acc}=fixture();
  const parent=doc.createNode().setTranslation([500,0,250]);scene.addChild(parent);
  parent.addChild(doc.createNode().setExtras({layer:'props',object_id:'sign'}).setMesh(doc.createMesh().addPrimitive(primitive)));
  const extension=doc.createExtension(EXTMeshGPUInstancing);
  parent.addChild(doc.createNode().setExtras({layer:'vegetation',object_id:'tree'})
    .setMesh(doc.createMesh().addPrimitive(primitive.clone()))
    .setExtension('EXT_mesh_gpu_instancing',extension.createInstancedMesh()
      .setAttribute('TRANSLATION',acc('VEC3',new Float32Array([0,0,.004, 10,0,0])))));
  const audit=auditVisual(doc);
  assert.equal(audit.triangles,3);assert.equal(audit.instances,2);
  assert.deepEqual(audit.layers,{props:1,vegetation:2});
  assert.equal(audit.violations,1);
  assert.match(audit.overlaps[0].first,/props/);assert.match(audit.overlaps[0].second,/vegetation/);
});
test('decoded mixed program triangles cannot ship', () => {
  const {doc,scene,primitive,acc}=fixture();
  primitive.setAttribute('_KIT_LAYOUT',acc('VEC3',new Float32Array([6,3,3, 6,3,3, 43,3,3])));
  scene.addChild(doc.createNode().setExtras({object_id:'wall'}).setMesh(doc.createMesh().addPrimitive(primitive)));
  assert.throws(()=>auditVisual(doc),/Mixed shipped facade instruction wall triangle 0/);
});
test('every facade component is checked, including non-finite later corners', () => {
  for (const invalid of [NaN, Infinity, 2]) {
    const {doc,scene,primitive,acc}=fixture();
    const values=new Float32Array(12).fill(1);values[11]=invalid;
    primitive.setAttribute('_KIT_DETAIL',acc('VEC4',values));
    scene.addChild(doc.createNode().setExtras({object_id:'wall'}).setMesh(doc.createMesh().addPrimitive(primitive)));
    assert.throws(()=>auditVisual(doc),/Mixed shipped facade instruction wall triangle 0 _KIT_DETAIL/);
  }
});
test('binding rejects stale ranges before creating shader attributes', () => {
  const {doc,primitive}=fixture();
  assert.throws(()=>bindProgram(doc,primitive,{object_id:'wall',facade_kit_walls:[{start:0,count:2}]},{},x=>x),/Unassigned/);
  assert.throws(()=>bindProgram(doc,primitive,{object_id:'wall',facade_kit_walls:[{start:0,count:2},{start:2,count:1}]},{},x=>x),/Mixed facade program triangle/);
});
