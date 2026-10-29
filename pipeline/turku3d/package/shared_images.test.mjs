import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { externalizeLibraryImages } from './shared_images.mjs';

// This exercises physical buffer relocation, including meshopt ranges between images.
test('splitting all texture payloads preserves geometry bytes and existing immutable assets', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'shared-images-'));
  try {
    const binary = Buffer.from([1,2,3,4, 11,12,13,14, 5,6,7,8, 21,22,23,24]);
    const doc = { buffers:[{byteLength:16}], images:[{bufferView:0},{bufferView:2}],
      bufferViews:[{buffer:0,byteOffset:0,byteLength:4},
        {buffer:0,byteOffset:4,byteLength:4,extensions:{EXT_meshopt_compression:{buffer:0,byteOffset:12,byteLength:4}}},
        {buffer:0,byteOffset:8,byteLength:4}] };
    const json = Buffer.from(JSON.stringify(doc).padEnd(Math.ceil(JSON.stringify(doc).length/4)*4, ' '));
    const header=Buffer.alloc(20), binHeader=Buffer.alloc(8);
    header.writeUInt32LE(0x46546c67,0); header.writeUInt32LE(2,4);
    header.writeUInt32LE(28+json.length+binary.length,8);header.writeUInt32LE(json.length,12);header.writeUInt32LE(0x4e4f534a,16);
    binHeader.writeUInt32LE(binary.length,0);binHeader.writeUInt32LE(0x004e4942,4);
    const file=path.join(root,'tile.glb'), shared=path.join(root,'shared');
    fs.writeFileSync(file,Buffer.concat([header,json,binHeader,binary]));
    const hashes=new Set([binary.subarray(0,4),binary.subarray(8,12)].map(b=>createHash('sha256').update(b).digest('hex')));
    const assets=externalizeLibraryImages(file,hashes,shared);
    assert.equal(assets.length,2);
    const bytes=fs.readFileSync(file), length=bytes.readUInt32LE(12);
    const result=JSON.parse(bytes.subarray(20,20+length)), data=bytes.subarray(28+length);
    assert.deepEqual([...data],[11,12,13,14,21,22,23,24]);
    assert.equal(result.bufferViews[1].byteOffset,0);
    assert.equal(result.bufferViews[1].extensions.EXT_meshopt_compression.byteOffset,4);
    for (const image of result.images) assert.match(image.uri,/\.\.\/shared\/[0-9a-f]{64}\.ktx2$/);
    const stat=fs.statSync(path.join(shared,assets[0]));
    externalizeLibraryImages(file,hashes,shared);
    assert.equal(fs.statSync(path.join(shared,assets[0])).mtimeMs,stat.mtimeMs);
  } finally { fs.rmSync(root,{recursive:true,force:true}); }
});

test('splits meshopt buffers without changing any referenced bytes', async () => {
  const {splitVisualBuffers}=await import('./split_buffers.mjs');
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'split-buffer-'));
  try {
    const binary=Buffer.from(Array.from({length:32},(_,i)=>i));
    const refs=[{buffer:0,byteOffset:0,byteLength:8},
      {buffer:1,byteLength:40,extensions:{EXT_meshopt_compression:{buffer:0,byteOffset:8,byteLength:12}}},
      {buffer:0,byteOffset:20,byteLength:12}];
    const doc={buffers:[{byteLength:32},{byteLength:40}],bufferViews:refs};
    const text=JSON.stringify(doc),json=Buffer.from(text.padEnd(Math.ceil(text.length/4)*4,' '));
    const header=Buffer.alloc(20),binHeader=Buffer.alloc(8);
    header.writeUInt32LE(0x46546c67,0);header.writeUInt32LE(2,4);header.writeUInt32LE(28+json.length+32,8);
    header.writeUInt32LE(json.length,12);header.writeUInt32LE(0x4e4f534a,16);
    binHeader.writeUInt32LE(32,0);binHeader.writeUInt32LE(0x004e4942,4);
    const file=path.join(root,'tile.glb'),shared=path.join(root,'shared');
    fs.writeFileSync(file,Buffer.concat([header,json,binHeader,binary]));
    const assets=splitVisualBuffers(file,shared,16);
    assert.equal(assets.length,3);
    const packed=fs.readFileSync(file),result=JSON.parse(packed.subarray(20));
    for(let i=0;i<refs.length;i++){
      const before=refs[i].extensions?.EXT_meshopt_compression ?? refs[i];
      const after=result.bufferViews[i].extensions?.EXT_meshopt_compression ?? result.bufferViews[i];
      const data=fs.readFileSync(path.join(shared,path.basename(result.buffers[after.buffer].uri)));
      assert.deepEqual(data.subarray(after.byteOffset,after.byteOffset+after.byteLength),binary.subarray(before.byteOffset,before.byteOffset+before.byteLength));
      assert.ok(data.length<=16);
    }
    assert.equal(result.buffers[1].byteLength,40); // Meshopt fallback buffer stays intact.
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});
