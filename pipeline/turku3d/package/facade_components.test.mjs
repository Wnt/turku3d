import assert from 'node:assert/strict';
import { test } from 'node:test';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { instanceFacadeComponents } from './facade_components.mjs';
const require = createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS ?? path.join(os.homedir(),'turku3d/tools/npm'),'package.json'));
const dep = name => import(pathToFileURL(require.resolve(name)).href);
const { Document, NodeIO } = await dep('@gltf-transform/core');
const { EXTMeshGPUInstancing } = await dep('@gltf-transform/extensions');

function fixture(cut=false) {
  const doc=new Document(), scene=doc.createScene(), buffer=doc.createBuffer(), material=doc.createMaterial();
  const sources=[];
  for (let i=0;i<3;i++) {
    const angle=i*.7, tangent=[Math.cos(angle),0,-Math.sin(angle)];
    const center=[10+i*4,5,3], size=[2,.2,1.1];
    const corners=[[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]];
    const faces=[[0,1,2,3],[5,4,7,6],[4,0,3,7],[1,5,6,2],[3,2,6,7],[4,5,1,0]];
    const pos=[],normals=[],indices=[];
    for (const face of faces) for (const index of face) {
      const [x,y,z]=corners[index].map((v,k)=>v*size[k]/2);
      pos.push(center[0]+x*tangent[0]-z*tangent[2],center[1]+y,center[2]+x*tangent[2]+z*tangent[0]);
      normals.push(0,1,0);
    }
    for(let j=0;j<6;j++) indices.push(j*4,j*4+3,j*4+2,j*4,j*4+2,j*4+1);
    if(cut && i===2) pos[0]+=.1;
    const acc=(type,array)=>doc.createAccessor().setType(type).setArray(array).setBuffer(buffer);
    const prim=doc.createPrimitive().setMaterial(material)
      .setAttribute('POSITION',acc('VEC3',new Float32Array(pos)))
      .setAttribute('NORMAL',acc('VEC3',new Float32Array(normals)))
      .setIndices(acc('SCALAR',new Uint32Array(indices)));
    scene.addChild(doc.createNode().setTranslation([250,0,0]).setExtras({component_box:{center,size,tangent}})
      .setMesh(doc.createMesh().addPrimitive(prim)));
    sources.push(pos);
  }
  return {doc,scene,sources};
}

test('instancing preserves rotated component vertices through glTF roundtrip',async()=>{
  const {doc,scene,sources}=fixture();
  instanceFacadeComponents(doc,scene,'t0_1_0',EXTMeshGPUInstancing);
  const io=new NodeIO().registerExtensions([EXTMeshGPUInstancing]);
  const read=await io.readBinary(await io.writeBinary(doc));
  const node=read.getRoot().listNodes().find(n=>n.getExtension('EXT_mesh_gpu_instancing'));
  assert.deepEqual(node.getTranslation(),[250,0,0]);
  const batch=node.getExtension('EXT_mesh_gpu_instancing');
  const pos=node.getMesh().listPrimitives()[0].getAttribute('POSITION');
  assert.equal(batch.getAttribute('TRANSLATION').getCount(),3);
  for(let i=0;i<3;i++) {
    const t=batch.getAttribute('TRANSLATION').getElement(i,[]);
    const s=batch.getAttribute('SCALE').getElement(i,[]);
    const q=batch.getAttribute('ROTATION').getElement(i,[]);
    const angle=2*Math.atan2(q[1],q[3]);
    for(let j=0;j<24;j++) {
      const [x,y,z]=pos.getElement(j,[]).map((v,k)=>v*s[k]);
      const p=[t[0]+Math.cos(angle)*x+Math.sin(angle)*z,t[1]+y,t[2]-Math.sin(angle)*x+Math.cos(angle)*z];
      for(let k=0;k<3;k++) assert.ok(Math.abs(p[k]-sources[i][j*3+k])<1e-4);
    }
  }
});

test('wall assembly cuts are preserved instead of restoring the original box',()=>{
  const {doc,scene}=fixture(true);
  instanceFacadeComponents(doc,scene,'t0_1_0',EXTMeshGPUInstancing);
  const nodes=doc.getRoot().listNodes().filter(n=>n.getMesh());
  assert.equal(nodes.length,2);
  assert.equal(nodes.find(n=>n.getExtension('EXT_mesh_gpu_instancing'))
    .getExtension('EXT_mesh_gpu_instancing').getAttribute('TRANSLATION').getCount(),2);
});

test('component instances retain source building metadata for raycast hits', () => {
  const {doc,scene} = fixture();
  scene.listChildren().forEach((node,i) => node.setExtras({...node.getExtras(),building_id:`house-${i}`,address:`Street ${i}`}));
  instanceFacadeComponents(doc,scene,'t0_1_0',EXTMeshGPUInstancing);
  const node = doc.getRoot().listNodes().find(n=>n.getExtension('EXT_mesh_gpu_instancing'));
  assert.deepEqual(node.getExtras().instance_objects.map(o=>[o.building_id,o.address]),
    [['house-0','Street 0'],['house-1','Street 1'],['house-2','Street 2']]);
});
