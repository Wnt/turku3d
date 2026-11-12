import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createHash} from 'node:crypto';
import {resolvePreparedAsset} from '../turku3d/scene/asset_store.mjs';
import {adaptQwenselRoof,slopeFrame} from '../turku3d/scene/qwensel_roof.mjs';
const dataBytes=fs.readFileSync(new URL('../turku3d/scene/qwensel_roof.json',import.meta.url)),data=JSON.parse(dataBytes);
const source=fs.readFileSync(resolvePreparedAsset(data.sourceGlbSha256+'.glb'));
const parse=b=>{const n=b.readUInt32LE(12);return {json:JSON.parse(b.subarray(20,20+n)),binary:b.subarray(28+n)};};
const maps={sourceGlbSha256:data.sourceGlbSha256,roofDataSha256:createHash('sha256').update(dataBytes).digest('hex'),surfaces:Object.fromEntries(['clay','brick'].map(kind=>[kind,Object.fromEntries(['albedo','normal','orm'].map((slot,i)=>[slot,{sha256:String(i+1).repeat(64),uri:`../shared/${String(i+1).repeat(64)}.ktx2`,extent:[1024,1024]}]))]))};
test('roof detail retains every source byte and all accepted frontage/plane attributes',()=>{
 const before=parse(source),r=adaptQwenselRoof(source,maps),after=parse(r.bytes);
 assert.deepEqual(after.binary.subarray(0,before.binary.length),before.binary);
 assert.equal(r.report.selectedTriangles,25);assert.equal(r.report.chimneys.length,2);
 assert.deepEqual(after.json.materials.slice(0,before.json.materials.length),before.json.materials);
 assert.deepEqual(after.json.images.slice(0,before.json.images.length),before.json.images);
 let changed=0;
 before.json.meshes.forEach((m,i)=>m.primitives.forEach((p,j)=>{
  const q=after.json.meshes[i].primitives[j];
  if(before.json.materials[p.material].extras?.material_key==='landmark_roof_tile'){
   changed++;assert.equal(q.indices,p.indices);for(const [name,id] of Object.entries(p.attributes))if(name!=='TEXCOORD_0')assert.equal(q.attributes[name],id);
   assert.notEqual(q.attributes.TEXCOORD_0,p.attributes.TEXCOORD_0);
  }else assert.deepEqual(q,p);
 }));assert.equal(changed,1);
 const extra=after.json.meshes.flatMap((m,i)=>m.primitives.slice(before.json.meshes[i].primitives.length));
 assert.equal(extra.length,6);assert.ok(extra.every(p=>p.extras.collision==='passable'));
 for(const c of r.report.chimneys){assert.ok(c.topM>c.roofBaseM&&c.topM-c.roofBaseM<2);assert.equal(c.triangles,34);}
});
test('metric slope frame retains a common tile direction across triangles and rejects stale inputs',()=>{
 const n=[.317,.770,.554],{t,v}=slopeFrame(n),dot=(a,b)=>a.reduce((s,x,i)=>s+x*b[i],0);
 assert.ok(Math.abs(dot(t,v))<1e-12&&Math.abs(dot(t,n))<1e-12&&Math.abs(dot(v,n))<1e-12);
 assert.ok(v[1]>0);assert.ok(Math.abs(dot(t,t)-1)<1e-12&&Math.abs(dot(v,v)-1)<1e-12);
 // Moving one physical repeat along a contour must move exactly one U, not V.
 const step=t.map(x=>x*.92);assert.ok(Math.abs(dot(step,t)/.92-1)<1e-12);assert.ok(Math.abs(dot(step,v))<1e-12);
 assert.throws(()=>adaptQwenselRoof(Buffer.from('other'),maps),/source differs/);
 const bad=structuredClone(maps);bad.surfaces.clay.normal.extent=[900,210];assert.throws(()=>adaptQwenselRoof(source,bad),/aligned/);
});
