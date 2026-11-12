import {test} from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import {adaptOffice,OFFICE_PALETTE} from '../turku3d/scene/office_appearance.mjs';
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
function fixture(){
 const document={asset:{version:'2.0'},buffers:[{byteLength:0}],bufferViews:[],accessors:[],images:[{uri:'../shared/unchanged.ktx2'}],materials:[{}, {extras:{material_key:'roof_ortho'},pbrMetallicRoughness:{baseColorTexture:{index:0}}}],meshes:[]};
 const chunks=[];let offset=0;
 function attr(values,dim){const b=Buffer.alloc(values.length*4);values.forEach((v,i)=>b.writeFloatLE(v,i*4));const view=document.bufferViews.length;document.bufferViews.push({buffer:0,byteOffset:offset,byteLength:b.length});chunks.push(b);offset+=b.length;const index=document.accessors.length;document.accessors.push({bufferView:view,componentType:5126,count:values.length/dim,type:{3:'VEC3',4:'VEC4'}[dim]});return index;}
 const attributes={POSITION:attr([-20,4,0,20,4,0],3)};
 for(const name of ['_KIT_WALL','_KIT_BASE','_KIT_TRIM'])attributes[name]=attr([.1,.2,.3,.1,.2,.3],3);
 attributes._KIT_FRAME=attr([.1,.2,.3,7,.1,.2,.3,7],4);attributes._KIT_LAYOUT=attr([18,2,7,1,18,2,7,1],4);
 document.meshes=[{primitives:[{attributes,material:0},{attributes:{POSITION:attributes.POSITION},material:1}]}];document.buffers[0].byteLength=offset;
 const json=Buffer.from(JSON.stringify(document)),jp=Buffer.alloc((json.length+3)&~3,32);json.copy(jp);const bin=Buffer.concat(chunks),out=Buffer.alloc(28+jp.length+bin.length);out.writeUInt32LE(0x46546c67,0);out.writeUInt32LE(2,4);out.writeUInt32LE(out.length,8);out.writeUInt32LE(jp.length,12);out.writeUInt32LE(0x4e4f534a,16);jp.copy(out,20);out.writeUInt32LE(bin.length,20+jp.length);out.writeUInt32LE(0x004e4942,24+jp.length);bin.copy(out,28+jp.length);return {out,document,bin};
}
test('appearance retains original binary, layout and image dependencies, excluding grey wing',()=>{
 const f=fixture(),r=adaptOffice(f.out,sha(f.out)),length=r.bytes.readUInt32LE(12),j=JSON.parse(r.bytes.subarray(20,20+length)),bin=r.bytes.subarray(28+length);
 assert.deepEqual(bin.subarray(0,f.bin.length),f.bin);assert.deepEqual(j.images,f.document.images);
 const old=f.document.meshes[0].primitives[0].attributes,a=j.meshes[0].primitives[0].attributes;
 assert.equal(a.POSITION,old.POSITION);assert.equal(a._KIT_LAYOUT,old._KIT_LAYOUT);
 const acc=j.accessors[a._KIT_WALL],view=j.bufferViews[acc.bufferView];
 for(let k=0;k<3;k++){assert.ok(Math.abs(bin.readFloatLE(view.byteOffset+k*4)-[.1,.2,.3][k])<1e-6);assert.ok(Math.abs(bin.readFloatLE(view.byteOffset+12+k*4)-OFFICE_PALETTE.wall[k])<1e-6);}
 const frame=j.accessors[a._KIT_FRAME],fv=j.bufferViews[frame.bufferView];assert.equal(bin.readFloatLE(fv.byteOffset+7*4),7);
 assert.deepEqual(j.materials[1].pbrMetallicRoughness.baseColorTexture,{index:0});assert.equal(r.report.changedVertices,1);
});
test('unapproved source is rejected before mutation',()=>assert.throws(()=>adaptOffice(fixture().out),/approved authored grid/));
