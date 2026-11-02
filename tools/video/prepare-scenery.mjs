#!/usr/bin/env node
// Prepare exactly the game's visible scenery footprints without holding a browser slot.
import fs from 'node:fs';
import {createServer} from '../../game/node_modules/vite/dist/node/index.js';
import {poseAt} from './paths.mjs';
const args=process.argv.slice(2), opt=(key,fallback)=>args.includes(key)?args[args.indexOf(key)+1]:fallback;
const service=opt('--service','http://127.0.0.1:5964'),out=opt('--out','');
const config=JSON.parse(fs.readFileSync(opt('--shots',new URL('./comparison-shots.json',import.meta.url))));
const server=await createServer({configFile:false,root:new URL('../../game',import.meta.url).pathname,server:{middlewareMode:true,hmr:false},appType:'custom'});
const {sceneryTiles,regionalTiles,groundHalfAngle}=await server.ssrLoadModule('/src/world/sceneryView.ts');
await server.close();
const poses=config.shots.flatMap(s=>Array.from({length:121},(_,i)=>poseAt(s,i/120)));
if(opt('--cameras',''))for(const c of JSON.parse(fs.readFileSync(opt('--cameras'))).cameras)poses.push(c.cam?Object.fromEntries(['E','N','H','yaw','pitch','fov'].map((k,i)=>[k,Number(c.cam.split(',')[i])])):c);
const ids=new Set();
for(const p of poses){
 const yaw=p.yaw*Math.PI/180,pitch=p.pitch*Math.PI/180;
 // Include the full-width evidence poses as well as the narrower comparison panels.
 const v={x:p.E-23459500,y:p.H,z:6704500-p.N,dx:Math.sin(yaw),dz:-Math.cos(yaw),halfAngle:groundHalfAngle(Math.abs(Math.sin(pitch)),Math.abs(Math.cos(pitch)),p.fov,16/9)};
 for(const id of [...regionalTiles(v),...sceneryTiles(4000,v)])ids.add(id);
}
const sources=[],tiles=[...ids];let generation;
for(let i=0;i<tiles.length;i+=128){
 const r=await fetch(`${service}/coarse/scenery?tiles=${tiles.slice(i,i+128).join(',')}`,{signal:AbortSignal.timeout(180000)});if(!r.ok)throw Error(`Catalogue ${r.status}`);
 const body=await r.json();if(body.missing.length)throw Error(`Missing sources: ${body.missing}`);
 if(generation&&generation!==body.generation)throw Error('Source generation changed');generation=body.generation;sources.push(...body.tiles);
}
let next=0,done=0;const receipts=[];
async function worker(){while(next<sources.length){const source=sources[next++],start=Date.now();
 while(true){const r=await fetch(`${service}/coarse/scenery/${source.key}.json`,{signal:AbortSignal.timeout(30000)});
  if(r.ok&&r.status!==202){const manifest=await r.json();if(manifest.stale||manifest.pass_kind!=='full')throw Error(`Incomplete ${source.tile}`);receipts.push({source,manifest});break;}
  if(r.status!==202&&r.status!==429)throw Error(`${source.tile}: HTTP ${r.status}`);
  if(Date.now()-start>1800000)throw Error(`Timed out: ${source.tile}`);await new Promise(resolve=>setTimeout(resolve,2000));
 }
 ++done;if(done%20===0||done===sources.length)console.log(`${new Date().toISOString()} Scenery ${done}/${sources.length}`);
 if(out)fs.writeFileSync(out,JSON.stringify({generation,ready:done===sources.length,done,total:sources.length,receipts},null,1));
}}
console.log(`Preparing ${sources.length} visible scenery nodes`);await Promise.all(Array.from({length:4},worker));
