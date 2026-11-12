import {test} from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import {repairStoreys} from '../turku3d/scene/facade_storeys.mjs';
function fixture(){
 const doc={asset:{version:'2.0'},buffers:[{byteLength:0}],bufferViews:[],accessors:[],images:[{uri:'same.ktx2'}],materials:[{pbrMetallicRoughness:{baseColorFactor:[.4,.3,.2,1]}}],meshes:[]},chunks=[];let size=0;
 function attr(values,dim){const raw=Buffer.alloc(values.length*4);values.forEach((v,i)=>raw.writeFloatLE(v,i*4));const view=doc.bufferViews.length;doc.bufferViews.push({buffer:0,byteOffset:size,byteLength:raw.length});chunks.push(raw);size+=raw.length;const id=doc.accessors.length;doc.accessors.push({bufferView:view,componentType:5126,count:values.length/dim,type:'VEC'+dim});return id;}
 const attrs={POSITION:attr([0,1,0,1,1,0,0,2,0],3),_KIT_LAYOUT:attr([28,3,.7,0,46,3,.7,0,24,4,.7,0],4),_KIT_FLOORS:attr([.7,.7,2,3.3,.26666667,2,.001,.7,0],3),_KIT_DETAIL:attr([0,0,0,0,0,0,0,0,0,0,0,1],4)};
 doc.meshes=[{primitives:[{attributes:attrs,material:0}]}];doc.buffers[0].byteLength=size;
 let json=Buffer.from(JSON.stringify(doc));json=Buffer.concat([json,Buffer.alloc((-json.length)&3,32)]);const bin=Buffer.concat(chunks),out=Buffer.alloc(28+json.length+bin.length);
 out.writeUInt32LE(0x46546c67);out.writeUInt32LE(2,4);out.writeUInt32LE(out.length,8);out.writeUInt32LE(json.length,12);out.writeUInt32LE(0x4e4f534a,16);json.copy(out,20);out.writeUInt32LE(bin.length,20+json.length);out.writeUInt32LE(0x004e4942,24+json.length);bin.copy(out,28+json.length);
 const recipe={sourceSha256:crypto.createHash('sha256').update(out).digest('hex'),sourceStoreyM:.7,correctedStoreyM:3,correctedEnvelopeM:21,storeys:7};return{doc,bin,out,recipe};
}
test('repairs fallback only, retaining reviewed ground and observed patches',()=>{
 const f=fixture(),result=repairStoreys(f.out,f.recipe),n=result.bytes.readUInt32LE(12),j=JSON.parse(result.bytes.subarray(20,20+n)),bin=result.bytes.subarray(28+n),a=j.meshes[0].primitives[0].attributes;
 assert.deepEqual(bin.subarray(0,f.bin.length),f.bin);assert.deepEqual(j.images,f.doc.images);assert.deepEqual(j.materials,f.doc.materials);assert.equal(a.POSITION,f.doc.meshes[0].primitives[0].attributes.POSITION);assert.equal(result.report.changedVertices,2);assert.equal(result.report.preservedPatchVertices,1);
 function values(index){const acc=j.accessors[index],v=j.bufferViews[acc.bufferView],dim=Number(acc.type.at(-1));return Array.from({length:acc.count*dim},(_,i)=>bin.readFloatLE(v.byteOffset+i*4));}
 const layout=values(a._KIT_LAYOUT),floors=values(a._KIT_FLOORS);assert.equal(layout[2],3);assert.equal(layout[6],3);assert.ok(Math.abs(layout[10]-.7)<1e-6);assert.equal(floors[0],3);assert.equal(floors[1],3);assert.ok(Math.abs(floors[3]-3.3)<1e-6);assert.ok(Math.abs(floors[4]-2.95)<1e-6);assert.ok(Math.abs(floors[6]-.001)<1e-6);assert.ok(Math.abs(floors[7]-.7)<1e-6);
});
test('rejects unpinned and unsupported corrections before publication',()=>{const f=fixture();assert.throws(()=>repairStoreys(f.out,{...f.recipe,sourceSha256:'no'}),/pinned/);assert.throws(()=>repairStoreys(f.out,{...f.recipe,correctedStoreyM:1}),/Unsupported/);});
