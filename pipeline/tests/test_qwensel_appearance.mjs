import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createHash} from 'node:crypto';
import {resolvePreparedAsset} from '../turku3d/scene/asset_store.mjs';
import {adaptQwensel} from '../turku3d/scene/qwensel_appearance.mjs';
const source=fs.readFileSync(resolvePreparedAsset('07db3032d1dc14cc4de898663ce5588685308e8643a074200b0ccf8443e20ab4.glb'));
const parse=b=>{const n=b.readUInt32LE(12);return {json:JSON.parse(b.subarray(20,20+n)),binary:b.subarray(28+n)};};
const maps={sourceGlbSha256:'07db3032d1dc14cc4de898663ce5588685308e8643a074200b0ccf8443e20ab4',frontageDataSha256:createHash('sha256').update(fs.readFileSync(new URL('../turku3d/scene/qwensel_frontage.json',import.meta.url))).digest('hex'),maps:Object.fromEntries(['albedo','normal','orm'].map((slot,i)=>[slot,{sha256:String(i+1).repeat(64),uri:`../shared/${String(i+1).repeat(64)}.ktx2`}] ))};
test('only two river-wall triangles pairs change material; every original binary byte and other material remains',()=>{
 const original=parse(source),result=adaptQwensel(source,maps),after=parse(result.bytes);
 assert.deepEqual(after.binary.subarray(0,original.binary.length),original.binary);
 assert.equal(result.report.selectedTriangles,4);assert.equal(result.report.changedPrimitives,1);
 assert.deepEqual(after.json.materials.slice(0,original.json.materials.length),original.json.materials);
 assert.deepEqual(after.json.images.slice(0,original.json.images.length),original.json.images);
 assert.equal(after.json.images.length,original.json.images.length+3);
 for(let i=0;i<3;i++)assert.deepEqual(after.json.meshes[i],original.json.meshes[i]);
 const before=original.json.meshes[3].primitives[0],retained=after.json.meshes[3].primitives[0],front=after.json.meshes[3].primitives[1];
 assert.deepEqual(retained.attributes,before.attributes);
 for(const key of ['POSITION','NORMAL','COLOR_1'])assert.equal(front.attributes[key],before.attributes[key]);
 assert.equal(after.json.accessors[retained.indices].count+after.json.accessors[front.indices].count,original.json.accessors[before.indices].count);
 assert.equal(after.json.accessors[front.indices].count,12);
});
test('unknown source and mutable image dependency are rejected',()=>{
 assert.throws(()=>adaptQwensel(Buffer.from('different'),maps),/source differs/);
 const stale=structuredClone(maps);stale.frontageDataSha256='f'.repeat(64);assert.throws(()=>adaptQwensel(source,stale),/layout differs/);
 const invalid=structuredClone(maps);invalid.maps.albedo.uri='current.ktx2';assert.throws(()=>adaptQwensel(source,invalid),/immutable/);
});
