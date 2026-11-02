// Read-only production relay. Cache only successful, content-addressed assets outside git.
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';

export function publishedProxy({upstream='http://127.0.0.1:5195',cacheDir=path.join(os.homedir(),`turku3d/loops/${process.env.TURKU3D_CAPTURE_OWNER??'video'}/published-cache`)}={}) {
 const pending=new Map();
 async function request(url,method,headers) {
  for(let attempt=0;attempt<3;attempt++) {
   try {
    const result=await new Promise((resolve,reject)=>{
     const r=http.request(url,{method,headers:{...headers,host:url.host}},response=>{
      const chunks=[];response.on('data',chunk=>chunks.push(chunk));response.on('error',reject);
      response.on('end',()=>resolve({status:response.statusCode,headers:response.headers,body:Buffer.concat(chunks)}));
     });
     r.setTimeout(20000,()=>r.destroy(Error('Published asset request timed out')));
     r.on('error',reject);r.end();
    });
    if(result.status<500&&result.status!==408&&result.status!==429)return result;
    if(attempt===2)return result;
   } catch(error) {if(attempt===2)throw error;}
  }
 }
 async function cached(url,headers) {
  const key=createHash('sha256').update(url.href).digest('hex'),file=path.join(cacheDir,key);
  try {
   const meta=JSON.parse(await fs.readFile(file+'.json','utf8')),body=await fs.readFile(file+'.body');
   if(meta.sha256===createHash('sha256').update(body).digest('hex'))return {...meta,body};
  } catch {} // Missing/incomplete local entries are fetched again; never serve partial bytes.
  if(pending.has(key))return pending.get(key);
  const task=(async()=>{
   const result=await request(url,'GET',headers);
   if(result.status===200) {
    await fs.mkdir(cacheDir,{recursive:true});
    const tmp=file+'.tmp';await fs.writeFile(tmp,result.body);await fs.rename(tmp,file+'.body');
    await fs.writeFile(file+'.json',JSON.stringify({status:200,headers:result.headers,sha256:createHash('sha256').update(result.body).digest('hex')}));
   }
   return result;
  })();
  pending.set(key,task);try{return await task;}finally{pending.delete(key);}
 }
 return http.createServer(async(req,res)=>{
  if(req.method!=='GET'&&req.method!=='HEAD'){res.writeHead(405);res.end();return;}
  try {
   const url=new URL(req.url,upstream);
   const immutable=req.method==='GET'&&!req.headers.range&&/\/[a-f0-9]{64}\.(glb|ktx2|png|jpg|jpeg|col|bin)$/.test(url.pathname);
   const result=await(immutable?cached(url,req.headers):request(url,req.method,req.headers));
   res.writeHead(result.status,result.headers);res.end(result.body);
  } catch(error) {res.writeHead(502);res.end(error.message);}
 });
}

if(process.argv[1]&&import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href)
 publishedProxy({upstream:process.argv[3]}).listen(Number(process.argv[2]??5965),'127.0.0.1');
