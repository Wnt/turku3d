// Sandbox-only read-through relay: deployed generator responses remain unmodified.
import http from 'node:http';
import https from 'node:https';
import fs from 'node:fs';
import path from 'node:path';
import {port,workRoot} from '../dev/environment.mjs';
const root=path.join(workRoot,'video/evidence');fs.mkdirSync(root,{recursive:true});
http.createServer((req,res)=>{
 if(!req.url.startsWith('/coarse/')){res.writeHead(403);return res.end();}
 const start=Date.now();
 const up=https.request(new URL(req.url === '/coarse/status' ? '/coarse/health' : req.url,'https://turku.lab.madekivi.fi'),{method:req.method,headers:{accept:'*/*'}},r=>{
  res.writeHead(r.statusCode,r.headers);r.pipe(res);
  r.on('end',()=>fs.appendFileSync(path.join(root,'relay.jsonl'),JSON.stringify({at:new Date(start).toISOString(),url:req.url,status:r.statusCode,ms:Date.now()-start})+'\n'));
 });
 up.on('error',e=>{if(!res.headersSent)res.writeHead(502);res.end(e.message);});req.pipe(up);
}).listen(port(5),'127.0.0.1',()=>console.log(`Generator relay ${port(5)}`));
