// ABABAB evidence through the shared capture harness: one browser, one active page at a time.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {execFileSync} from 'node:child_process';
import {chromium} from '../../game/node_modules/playwright-core/index.mjs';

export async function capturePair({cdp,before,after,out,cameras,timeout}) {
 const browser=await chromium.connectOverCDP(cdp),context=browser.contexts()[0],pages=[],sessions=[],results=[];
 const residency=()=>[...window.__game.lights.source.entries].filter(([,e])=>e.model&&!e.preview)
  .map(([id,e])=>({id,hash:e.visualHash,pass:e.pass})).sort((a,b)=>a.id.localeCompare(b.id));
 const freeze=async side=>{for(let i=0;i<pages.length;i++)await sessions[i].send('Page.setWebLifecycleState',{state:i===side?'active':'frozen'});};
 fs.mkdirSync(out,{recursive:true});
 for(const page of context.pages())await page.close();
 try {
  for(const camera of cameras) {
   const pose=Object.fromEntries(['E','N','H','yaw','pitch','fov'].map((key,i)=>[key,Number(camera.cam.split(',')[i])]));
   for(let side=0;side<2;side++) {
    if(!pages[side]) {
     const p=await context.newPage();pages[side]=p;sessions[side]=await context.newCDPSession(p);
     await p.setViewportSize({width:1280,height:720});await freeze(side);
     const url=new URL(side?after:before);
     for(const [k,v] of Object.entries({cam:camera.cam,time:camera.time??'13:00',quality:'medium',aa:'taa',hud:0,traffic:0,walkers:0}))url.searchParams.set(k,v);
     await p.goto(url.href,{waitUntil:'domcontentloaded',timeout:120000});
     await p.waitForFunction(()=>!!window.__qaMove,null,{timeout});
    }
    await freeze(side);
    const p=pages[side];
    await p.evaluate(({pose,hour,weather})=>{window.__render.beforeFrame=undefined;window.__render.setHour(hour);window.__dev.setWeather(weather,0);window.__qaMove(pose);},
     {pose,hour:Number((camera.time??'13:00').split(':')[0]),weather:camera.weather??'clear'});
    const started=Date.now();let state;
    while(Date.now()-started<timeout) {
     state=await p.evaluate(()=>({ready:window.__qaReady,error:window.__qaError,scenery:window.__game.scenery.progress,pending:window.__game.vegetation?.pending?.size??0}));
     if(state.error?.startsWith('timeout: tiles did not settle'))await p.evaluate(pose=>window.__qaMove(pose),pose);
     else if(state.error)throw Error(state.error);
     const s=state.scenery,far=side?s.ready:!s.pending&&!s.errors&&!s.cataloguePending&&s.loaded===s.total;
     if(state.ready&&far&&!state.pending)break;
     await p.waitForTimeout(1000);
    }
    if(!state.ready||(side&&!state.scenery.ready))throw Error(`Unsettled ${camera.id}/${side}: ${JSON.stringify(state)}`);
    await p.evaluate(()=>window.__render.beforeFrame=()=>false);
    console.log(`Ready ${camera.id}/${side?'B':'A'}`);
   }
   const products=await Promise.all(pages.map(p=>p.evaluate(residency)));
   if(JSON.stringify(products[0])!==JSON.stringify(products[1]))throw Error(`Near residency mismatch ${camera.id}: ${JSON.stringify(products)}`);
   const samples=[];
   for(let round=0;round<3;round++)for(let side=0;side<2;side++) {
    await freeze(side);
    const load={uptime:execFileSync('uptime',{encoding:'utf8'}).trim(),loadAverage:os.loadavg()};
    const measurement=await pages[side].evaluate(()=>new Promise(resolve=>{
     const r=window.__render,intervals=[];let count=0,last=0;
     r.beforeFrame=undefined;
     r.afterFrame=async()=>{
      const now=performance.now();if(count===30)r.perf.reset();
      if(count>30)intervals.push(now-last);last=now;
      if(++count<241)return;
      r.afterFrame=undefined;r.beforeFrame=()=>false;await r.perf.flush();
      const meanMs=intervals.reduce((a,b)=>a+b,0)/intervals.length;
      resolve({meanMs,fps:1000/meanMs,...r.perf.snapshot()});
     };
    }));
    const resident=await pages[side].evaluate(residency);
    if(JSON.stringify(resident)!==JSON.stringify(products[side]))throw Error(`Residency changed during measurement ${camera.id}/${side}`);
    samples.push({round,side:side?'B':'A',load,...measurement});
    console.log(JSON.stringify({pose:camera.id,...samples.at(-1)}));
    if(round===0)await pages[side].screenshot({path:path.join(out,`${camera.id}-${side?'B':'A'}.jpg`),type:'jpeg',quality:92});
   }
   results.push({camera,products:products[0],samples});
   fs.writeFileSync(path.join(out,'paired-performance.json'),JSON.stringify({before,after,viewport:[1280,720],quality:'medium',order:'ABABAB',results},null,1));
  }
 }finally {for(const page of pages)await page.close().catch(()=>{});await browser.close();}
}
