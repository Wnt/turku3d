import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { require, io, readSource } from './scenery-source.mjs';
import { programFields, openingKind, openingAtlas } from './scenery-openings.mjs';
import { programMask, legacyGrid, auditDocuments } from './scenery-window-audit.mjs';
const {Document}=require('@gltf-transform/core'),sharp=require('sharp');
const fields={_KIT_LAYOUT:[0,3,3],_KIT_WALL:[.5,.4,.3],_KIT_TRIM:[.8,.8,.8],_KIT_BASE:[.5,.4,.3],
  _KIT_EXTENT:[0,0,6,9],_KIT_FLOORS:[3,3,1],_KIT_OPENING:[.5,.5,.25,0],_KIT_BANDS:[0,0,0,0],
  _KIT_DETAIL:[0,0,0,0],_KIT_GLAZING:[0,0,0,-1],_KIT_FRAME:[.8,.8,.8,0]};
test('opening envelope honours blank walls, real floor origin and pointed measured patches',()=>{
  assert.equal(programMask(fields,[1.5,1.5]),1);assert.equal(programMask(fields,[0,1.5]),0);
  assert.equal(programMask({...fields,_KIT_LAYOUT:[42,3,3]},[1.5,1.5]),0);
  assert.equal(programMask({...fields,_KIT_EXTENT:[0,2,6,9]},[1.5,1.5]),0);
  const patch={...fields,_KIT_EXTENT:[0,0,3,6],_KIT_DETAIL:[0,0,0,13]};
  assert.equal(programMask(patch,[1.5,5.5]),1);assert.equal(programMask(patch,[.1,5.5]),0);
  assert.equal(legacyGrid([1.5,1.5,0],[0,0,1]),1);
});

function wall(doc,material,id,offset,program){
  const buffer=doc.getRoot().listBuffers()[0],acc=(type,values)=>doc.createAccessor().setType(type).setArray(new Float32Array(values)).setBuffer(buffer);
  const primitive=doc.createPrimitive().setMaterial(material)
    .setAttribute('POSITION',acc('VEC3',[offset,0,0,offset+6,0,0,offset+6,9,0,offset,9,0]))
    .setAttribute('TEXCOORD_0',acc('VEC2',program?[0,0,6,0,6,9,0,9]:[0,0,1,0,1,1,0,1]))
    .setIndices(doc.createAccessor().setType('SCALAR').setArray(new Uint16Array([0,1,2,0,2,3])).setBuffer(buffer));
  if(program)for(const key of programFields){const values=program[key];primitive.setAttribute(key,acc(`VEC${values.length}`,Array.from({length:4},()=>values).flat()));}
  doc.getRoot().listScenes()[0].addChild(doc.createNode(id).setExtras({layer:'buildings',surface:'wall',building_id:id,landmark_description:program?undefined:'measured'})
    .setMesh(doc.createMesh().addPrimitive(primitive)));
  return primitive;
}
test('shipped far walls preserve authored programs, blank walls and landmark texture openings',async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'far-openings-'));
  try{
    const doc=new Document();doc.createBuffer();doc.createScene();const material=doc.createMaterial('program').setExtras({facade_kit:{version:5}});
    wall(doc,material,'ordinary',0,fields);wall(doc,material,'blank',10,{...fields,_KIT_LAYOUT:[42,3,3]});
    const width=16,height=16,colour=Buffer.alloc(width*height*3,180),orm=Buffer.alloc(width*height*3,220);
    for(let y=4;y<12;y++)for(let x=6;x<10;x++){const i=(y*width+x)*3;colour.fill(24,i,i+3);orm[i+1]=20;}
    const image=async(name,data)=>doc.createTexture(name).setMimeType('image/png').setImage(await sharp(data,{raw:{width,height,channels:3}}).png().toBuffer());
    const mapped=doc.createMaterial('landmark').setBaseColorTexture(await image('colour',colour)).setMetallicRoughnessTexture(await image('orm',orm));
    const primitive=wall(doc,mapped,'landmark',20);
    assert.equal(openingKind(primitive,{surface:'wall'}),'mapped');assert.equal(openingKind(primitive,{surface:'roof'}),'none');
    const input=path.join(dir,'source.glb'),output=path.join(dir,'far.glb');await io.write(input,doc);
    execFileSync('node',[fileURLToPath(new URL('./scenery.mjs',import.meta.url)),input,output],{timeout:15000});
    const source=await readSource(input),far=await readSource(output);
    const before=await auditDocuments(source),after=await auditDocuments(source,far);
    assert.equal(after.buildings.length,3);
    assert.ok(before.invented_m2>20,JSON.stringify(before));
    assert.ok(after.invented_m2+after.missing_m2<1,JSON.stringify(after));
    assert.equal(after.buildings.find(b=>b.building==='blank').far_opening_m2,0);
    assert.ok(after.buildings.find(b=>b.building==='landmark').far_opening_m2>3);
    for(const row of after.buildings.filter(b=>b.bay_floor_offset_m!=null))assert.ok(row.bay_floor_offset_m<.01);
    const farProgram=far.getRoot().listNodes().find(n=>n.getExtras().layer==='buildings-program').getMesh().listPrimitives()[0];
    for(const key of programFields)assert.ok(farProgram.getAttribute(key));
  }finally{fs.rmSync(dir,{recursive:true,force:true});}
});
