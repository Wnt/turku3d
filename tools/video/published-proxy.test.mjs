import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {publishedProxy} from './published-proxy.mjs';

test('published relay retries failures and caches only complete immutable responses',async()=>{
 const cacheDir=await fs.mkdtemp(path.join(os.tmpdir(),'video-proxy-'));
 const counts=new Map(),bytes=Buffer.from([0,1,2,127,128,255]);
 const source=http.createServer((req,res)=>{
  const n=(counts.get(req.url)??0)+1;counts.set(req.url,n);
  if(req.url.includes('missing')){res.writeHead(404);res.end('missing');}
  else if(req.url.endsWith('.glb')&&n===1){res.writeHead(502);res.end('temporary');}
  else {res.writeHead(200,{'content-type':'application/octet-stream'});res.end(req.url.endsWith('.json')?String(n):bytes);}
 });
 const listen=s=>new Promise(resolve=>s.listen(0,'127.0.0.1',()=>resolve(`http://127.0.0.1:${s.address().port}`)));
 const upstream=await listen(source),proxy=publishedProxy({upstream,cacheDir}),base=await listen(proxy);
 try {
  const asset='/shared/'+'a'.repeat(64)+'.glb';
  for(let i=0;i<2;i++)assert.deepEqual(Buffer.from(await(await fetch(base+asset)).arrayBuffer()),bytes);
  assert.equal(counts.get(asset),2,'one502 and one200, second capture reads local cache');
  const body=(await fs.readdir(cacheDir)).find(f=>f.endsWith('.body'));
  await fs.writeFile(path.join(cacheDir,body),'truncated');
  assert.deepEqual(Buffer.from(await(await fetch(base+asset)).arrayBuffer()),bytes);
  assert.equal(counts.get(asset),3,'corrupt cache is fetched again');
  const manifest='/coarse/t0_0_0/manifest.json';
  assert.equal(await(await fetch(base+manifest)).text(),'1');
  assert.equal(await(await fetch(base+manifest)).text(),'2','manifest is never cached');
  assert.equal((await fetch(base+'/missing')).status,404);assert.equal(counts.get('/missing'),1);
  assert.equal((await fetch(base+asset,{method:'POST'})).status,405);assert.equal(counts.get(asset),3);
 } finally {proxy.closeAllConnections();source.closeAllConnections();await Promise.all([new Promise(r=>proxy.close(r)),new Promise(r=>source.close(r))]);await fs.rm(cacheDir,{recursive:true,force:true});}
});
