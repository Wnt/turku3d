import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import {resolvePreparedAsset} from '../turku3d/scene/asset_store.mjs';
test('authored input resolves by verified hash from external content store',()=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'prepared-assets-'));
 try{
  fs.mkdirSync(path.join(root,'shared'));const raw=Buffer.from('authored source'),sha=crypto.createHash('sha256').update(raw).digest('hex'),name=sha+'.glb',file=path.join(root,'shared',name);fs.writeFileSync(file,raw);
  assert.equal(resolvePreparedAsset(path.join(root,'old-repo',name),{assetRoot:root}),file);
  fs.writeFileSync(file,'corrupt');assert.throws(()=>resolvePreparedAsset(name,{assetRoot:root}),/hash mismatch/);
  assert.throws(()=>resolvePreparedAsset(path.join(root,'missing','named.glb'),{assetRoot:root}),/Missing authored/);
 }finally{fs.rmSync(root,{recursive:true,force:true});}
});
