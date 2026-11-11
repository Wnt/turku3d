#!/usr/bin/env node
// Trailer takes: drive the player car in the real game (Rapier vehicle physics, the game's input path) with a
// scripted key program or the autopilot, and record the vehicle state every frame. The takes are replayed
// frame-exactly under the trailer camera (trailer-capture.mjs), so pixels never depend on machine load.
//   BROWSER_CDP=... node tools/video/trailer-record.mjs --takes tools/video/trailer-takes.json [--only id,id]
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {chromium} from '../../game/node_modules/playwright-core/index.mjs';
const args=process.argv.slice(2),opt=(k,d)=>args.includes(k)?args[args.indexOf(k)+1]:d;
const config=JSON.parse(fs.readFileSync(opt('--takes',new URL('./trailer-takes.json',import.meta.url))));
const out=path.resolve(opt('--out',path.join(os.homedir(),'turku3d/video/2026-11-11-trailer/takes')));fs.mkdirSync(out,{recursive:true});
const selected=opt('--only','').split(',').filter(Boolean),base=opt('--base','http://127.0.0.1:5970/');
const log=m=>console.log(new Date().toISOString().slice(11,19),m);
const browser=await chromium.connectOverCDP(process.env.BROWSER_CDP),ctx=browser.contexts()[0];
await ctx.addInitScript(()=>Object.defineProperty(navigator,'getGamepads',{value:()=>[]}));
let page=null;
for(const p of ctx.pages())if(await p.evaluate(()=>window.__trailerPlay).catch(()=>false))page=p;
if(!page){
 page=await ctx.newPage();page.on('pageerror',e=>log('PAGE ERROR '+e.message.slice(0,200)));
 const first=config.takes[0].start,url=new URL(base);
 Object.entries({spawn:first.join(','),hud:0,quality:opt('--quality','low'),time:'2026-09-08T15:00:00+03:00',traffic:0,walkers:0,...(config.params??{})}).forEach(([k,v])=>url.searchParams.set(k,v));
 log(`loading ${url}`);await page.goto(url.href,{waitUntil:'domcontentloaded',timeout:120000});
 await page.waitForFunction(()=>window.__game?.ready,null,{timeout:300000});
 await page.locator('[data-option="new"]').click();
 await page.waitForFunction(()=>!window.__game.state().startScreen&&!window.__game.state().loading,null,{timeout:180000});
 await page.evaluate(()=>{window.__trailerPlay=true;});log('play session started');
}
for(const take of config.takes.filter(t=>!selected.length||selected.includes(t.id))){
 const result=await page.evaluate(async take=>{
  const g=window.__game,r=window.__render,sleep=ms=>new Promise(f=>setTimeout(f,ms));
  const x=take.start[0]-23459500,z=6704500-take.start[1],yaw=Math.PI/2-take.start[2]*Math.PI/180;
  r.beforeFrame=undefined;r.afterFrame=undefined;g.input.keys.clear();g.autopilot.cancel?.();
  if(!await g.relocateVehicle(x,z,yaw))return{error:'no drivable ground at start'};
  if(g.state().mode!=='car')g.enterVehicle('player');
  await sleep(take.settle??2500);
  const samples=[],t0=performance.now();let done;const finished=new Promise(f=>done=f);
  const program=take.program??[];
  if(take.autopilot){const a=take.autopilot;g.autopilot.goTo({x:a.to[0]-23459500,z:6704500-a.to[1]},{mode:'drive',takeover:false,...(a.options??{})});}
  r.beforeFrame=()=>{
   const t=(performance.now()-t0)/1000;
   if(program.length){let keys=[];for(const [at,k] of program)if(t>=at)keys=k;g.input.keys.clear();for(const k of keys)g.input.keys.add(k);}
   return true;
  };
  r.afterFrame=()=>{
   const t=(performance.now()-t0)/1000,v=g.physics.latest?.vehicle;
   if(v)samples.push({t,v:JSON.parse(JSON.stringify(v))});
   if(t>=take.seconds){r.beforeFrame=undefined;r.afterFrame=undefined;g.input.keys.clear();g.autopilot.cancel?.();done();}
  };
  await finished;
  return{samples,mode:g.state().mode};
 },take);
 if(result.error){log(`${take.id}: ${result.error}`);continue;}
 const s=result.samples,a=s[0].v.pos,b=s.at(-1).v.pos,max=Math.max(...s.map(e=>e.v.speed??0));
 let yawTravel=0,prev=null;for(const e of s){const q=e.v.rot,h=Math.atan2(2*(q.w*q.y+q.x*q.z),1-2*(q.y*q.y+q.z*q.z));if(prev!==null)yawTravel+=Math.abs(Math.atan2(Math.sin(h-prev),Math.cos(h-prev)));prev=h;}
 fs.writeFileSync(path.join(out,`${take.id}.json`),JSON.stringify({take,samples:s}));
 log(`${take.id}: ${s.length} samples over ${take.seconds}s (${(s.length/take.seconds).toFixed(0)} Hz), mode ${result.mode}, max ${(max*3.6).toFixed(0)} km/h, moved ${Math.hypot(b.x-a.x,b.z-a.z).toFixed(1)} m, turned ${(yawTravel*180/Math.PI).toFixed(0)} deg, end E ${(b.x+23459500).toFixed(1)} N ${(6704500-b.z).toFixed(1)} H ${b.y.toFixed(2)}`);
}
await browser.close();
