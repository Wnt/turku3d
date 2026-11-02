#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {chromium} from '../../game/node_modules/playwright-core/index.mjs';
import {launch,lease} from './comparison-browser.mjs';
import {poseAt,tilesForShot} from './paths.mjs';
const args=process.argv.slice(2), opt=(k,d)=>args.includes(k)?args[args.indexOf(k)+1]:d;
const config=JSON.parse(fs.readFileSync(opt('--shots',new URL('./comparison-shots.json',import.meta.url))));
const out=path.resolve(opt('--out',path.join(os.homedir(),'turku3d/video/2026-11-02')));
const preview=args.includes('--preview'), width=preview?480:960,height=preview?540:1080;
const selected=opt('--only','').split(',').filter(Boolean);
const urls=[opt('--left','http://127.0.0.1:5960/'),opt('--right','http://localhost:5220/')];
const cdpURL=opt('--cdp',''),sourceRevision=opt('--revision','');
const browser=cdpURL?await chromium.connectOverCDP(cdpURL):null;
const ctx=browser?browser.contexts()[0]:await launch(width,height);
await ctx.addInitScript(()=>Object.defineProperty(navigator,'getGamepads',{value:()=>[]}));
const pages=[], sessions=[], records=[new Map(),new Map()], failures=[[],[]], inflight=[new Set(),new Set()];
const dir=path.join(out,preview?'preview':'clips');fs.mkdirSync(dir,{recursive:true});
const iso=shot=>`${shot.date??"2026-09-08"}T${String(Math.floor(shot.hour)).padStart(2,'0')}:${String(Math.round(shot.hour%1*60)).padStart(2,'0')}:00+03:00`;
const log=m=>console.log(new Date().toISOString(),m);
async function setup(side,shot){
 const p=await ctx.newPage();pages[side]=p;await p.setViewportSize({width,height});
 if(side===1&&opt('--right-tiles',''))await p.route('**/coarse/**',async route=>{
  const target=new URL(route.request().url());const base=new URL(opt('--right-tiles'));target.protocol=base.protocol;target.host=base.host;
  const response=await route.fetch({url:target.href,timeout:120000});await route.fulfill({response});
 });
 p.on('pageerror',e=>failures[side].push(e.message));
 p.on('request',r=>{if(/\.(glb|ktx2|col|png|jpg)(\?|$)/.test(r.url()))inflight[side].add(r);});
 p.on('requestfinished',r=>inflight[side].delete(r));p.on('requestfailed',r=>{inflight[side].delete(r);if(!r.failure()?.errorText.includes('ABORTED'))failures[side].push(r.url()+': '+r.failure()?.errorText);});
 p.on('response',async r=>{if(/\/t0_[^/]+\/manifest.json/.test(r.url())){const j=await r.json().catch(()=>null);if(j?.visual)records[side].set(j.tile,j);}});
 sessions[side]=await ctx.newCDPSession(p);
 const url=new URL(urls[side]);Object.entries({cam:shot.from.join(','),time:iso(shot),hud:0,quality:'high',motion:1,traffic:0,walkers:0}).forEach(([k,v])=>url.searchParams.set(k,v));
 log(`loading ${side?'new':'production'} ${url}`);await p.goto(url.href,{waitUntil:'domcontentloaded',timeout:120000});
 await p.waitForFunction(()=>!!window.__qaMove,null,{timeout:300000});
 await p.addStyleTag({content:'body > :not(canvas){display:none!important}html,body,canvas{cursor:none!important}'});
 // Recover the existing streaming instance without replacing any game code or product.
 await p.evaluate(async()=>{
  if(window.__game.lights?.source){window.__videoCoarse=window.__game.lights.source;return;}
  const {CoarseTiles}=await import('/src/world/coarseTiles.ts');
  const update=CoarseTiles.prototype.update;
  CoarseTiles.prototype.update=function(...a){window.__videoCoarse=this;return update.apply(this,a);};
  window.__render.beforeFrame=undefined;
 });
 await p.waitForFunction(()=>!!window.__videoCoarse,null,{timeout:30000});
 await p.evaluate(()=>{
  const c=window.__videoCoarse;
  window.__video={nativeNow:performance.now.bind(performance),baseNow:performance.now(),baseDate:Date.UTC(2026,8,8,12),time:0,locked:false,update:c.update.bind(c),actor:null};
  c.update=(x,z)=>{if(!window.__video.locked)return window.__video.update(x,z,performance.now(),{x:0,z:0});};
  window.__render.beforeFrame=()=>false;
 });
}
async function settle(side,shot,u){
 const p=pages[side];
 await p.evaluate(({pose,time,weather,coverage})=>{
  const v=window.__video;v.locked=false;performance.now=v.nativeNow;window.__game.clock.simSeconds=0;
  const c=window.__videoCoarse;
  if(c.preload)c.preload(coverage.map(id=>{const [,i,j]=id.split('_').map(Number);return{x:i*250+125,z:j*250+125};}),0);
  window.__render.beforeFrame=undefined;window.__render.setTime(time);
  window.__dev?.setWeather?.(weather,0);window.__qaMove(pose);
 },{pose:poseAt(shot,u),time:iso(shot),weather:shot.weather,coverage:tilesForShot(shot)});
 const start=Date.now(),timeout=preview?1800000:600000;let state,settled=false;
 while(Date.now()-start<timeout){
  state=await p.evaluate(()=>({ready:window.__qaReady,error:window.__qaError,
   tiles:[...window.__videoCoarse.entries].map(([id,e])=>({id,pass:e.pass,hash:e.visualHash,model:!!e.model,preview:!!e.preview,state:e.state,stale:e.stale})),
   vegetation:window.__game.vegetation?.pending?.size??0,scenery:window.__game.scenery?.progress}));
  if(state.error?.startsWith('timeout: tiles did not settle')){await p.evaluate(pose=>{window.__game.clock.simSeconds=0;window.__qaMove(pose);},poseAt(shot,u));}
  else if(state.error)throw Error(`${shot.id} side ${side}: ${state.error}`);
  const wanted=tilesForShot({...shot,from:Object.values(poseAt(shot,u)),to:Object.values(poseAt(shot,u))});
  const full=wanted.every(id=>state.tiles.some(e=>e.id===id&&e.model&&e.pass==='full'&&!e.preview&&(preview||side===0||!e.stale)));
  const s=state.scenery,scenery=!s||(!s.cataloguePending&&!s.pending&&!s.errors&&s.loaded===s.total);
  if(state.ready&&full&&!state.vegetation&&scenery&&!inflight[side].size){settled=true;break;}
  await p.waitForTimeout(1000);
 }
 if(!settled)throw Error(`Tile settle timeout ${shot.id} side ${side}: ${JSON.stringify(state)}`);
 await p.evaluate(()=>window.__render.beforeFrame=()=>false);
 return state;
}
async function installActor(side,shot){
 return pages[side].evaluate(async shot=>{
  const v=window.__video;v.actor?.root?.removeFromParent();v.actor=null;
  if(!shot.mode)return null;
  const actorModule=await(await fetch('/src/actors/player.ts')).text();
  const threeURL=actorModule.match(/import \* as THREE from ["']([^"']+)["']/)?.[1];
  if(!threeURL)throw Error('Cannot locate the build’s Three.js module');
  const THREE=await import(threeURL);
  const scene=window.__render.environment.scene;
  if(shot.mode==='walk'){
   const {PlayerVisual}=await import('/src/actors/player.ts');
   const actor=new PlayerVisual(scene,async()=>null);await actor.load('/assets/character/player.glb',window.__render.renderer);v.actor=actor;
  }else{
   const {CarVisual}=await import('/src/actors/car.ts');const actor=new CarVisual(scene);await actor.load('/assets/car/sedan.glb');v.actor=actor;
  }
  v.THREE=THREE;
  // Ground queries use the rendered street surfaces, excluding the actor and vegetation.
  v.ground=(x,z)=>{
   const ray=new THREE.Raycaster(new THREE.Vector3(x,25,z),new THREE.Vector3(0,-1,0),0,40);
   const hits=ray.intersectObjects([...window.__videoCoarse.entries.values()].filter(e=>e.model).map(e=>e.model),true);
   const h=hits.find(h=>/ground|road|surface|pavement/i.test(h.object.name)&&h.face?.normal.y>.5);
   return h?.point.y??shot.from[2]-(shot.mode==='walk'?3.2:5.5);
  };
  return {mode:shot.mode,loaded:v.actor.loaded};
 },shot);
}
async function frame(side,shot,u,frame){
 const lo=Math.max(0,u-.0001),hi=Math.min(1,u+.0001),a=poseAt(shot,lo),b=poseAt(shot,hi);
 const speed=Math.hypot(b.E-a.E,b.N-a.N)/((hi-lo)*config.duration);
 const pose=poseAt(shot,u),distance=Math.hypot(pose.E-shot.from[0],pose.N-shot.from[1]);
 return pages[side].evaluate(({pose,frame,fps,mode,distance,speed})=>new Promise((resolve,reject)=>{
  const v=window.__video,r=window.__render,g=window.__game;
  v.locked=true;v.time=v.baseNow+10000+(frame+60)*1000/fps;performance.now=()=>v.time;
  if(g.dayClock.sharedNow)g.dayClock.sharedNow=()=>v.baseDate+(frame+60)*1000/fps;
  window.__dev?.rainEffects?.setPhaseSeconds?.((frame+60)/fps);
  window.__qaMove(pose);
  if(v.actor){
   const THREE=v.THREE,a=pose.yaw*Math.PI/180,d=mode==='walk'?5:9;
   const x=pose.E-23459500+Math.sin(a)*d,z=6704500-pose.N-Math.cos(a)*d,y=v.ground(x,z);
   if(mode==='walk')v.actor.update(1/fps,new THREE.Vector3(x,y,z),new THREE.Vector3(Math.sin(a)*speed,0,-Math.cos(a)*speed),true,0);
   else{
    const q=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,1,0),Math.PI/2-a);
    v.actor.update({pos:{x,y:y+.8,z},rot:q,wheels:[{pos:{x:1.35,y:-.45,z:.78}},{pos:{x:1.35,y:-.45,z:-.78}},{pos:{x:-1.35,y:-.45,z:.78}},{pos:{x:-1.35,y:-.45,z:-.78}}].map(w=>({...w,steer:0,rotation:distance/.32}))});
   }
  }
  const timer=setTimeout(()=>reject(Error('Frame submission timeout')),30000);
  r.beforeFrame=()=>{g.clock.simSeconds=(frame+60)/fps-1/fps;return true;};
  r.afterFrame=()=>{r.beforeFrame=()=>false;r.afterFrame=undefined;clearTimeout(timer);resolve({pose,simSeconds:g.clock.simSeconds,frames:g.clock.frames});};
 }),{pose,frame,fps:config.fps,mode:shot.mode,distance,speed});
}
try{
 // One browser, two long-lived, paused pages. Capture a segment's sides back to back.
 for(const old of ctx.pages())await old.evaluate(()=>{if(window.__render)window.__render.beforeFrame=()=>false;}).catch(()=>{});
 for(const shot of config.shots.filter(s=>!selected.length||selected.includes(s.id))){
  for(let side=0;side<2;side++){
   const name=`${shot.id}-${side?'right':'left'}`, file=path.join(dir,`${name}.mp4`),receipt=path.join(dir,`${name}.json`);
   const previous=fs.existsSync(receipt)?JSON.parse(fs.readFileSync(receipt)):null;
   if(((preview&&fs.existsSync(path.join(dir,`${name}-2.jpg`)))||(!preview&&fs.existsSync(file)))&&previous&&JSON.stringify(previous.shot)===JSON.stringify(shot)&&previous.width===width&&previous.height===height&&(preview||previous.sourceRevision===sourceRevision)&&!args.includes('--force')){log(`reuse ${name}`);continue;}
   lease('touch');for(let other=0;other<2;other++)if(pages[other]&&other!==side)await sessions[other].send('Page.setWebLifecycleState',{state:'frozen'});if(!pages[side])await setup(side,shot);
   for(let other=0;other<2;other++)if(pages[other])await sessions[other].send('Page.setWebLifecycleState',{state:other===side?'active':'frozen'});
   log(`settling ${name}`);
   // Inspect both endpoints and return to the first. Avoid reloads and reuse compiled meshes.
   for(const u of [1,0])await settle(side,shot,u);
   const actors=await installActor(side,shot);
   for(let k=-32;k<0;k++)await frame(side,shot,0,k);
   const resident=await pages[side].evaluate(()=>[...window.__videoCoarse.entries].filter(([,e])=>e.model).map(([id,e])=>({id,hash:e.visualHash,pass:e.pass,preview:!!e.preview})));
   const manifests=[];
   for(const tile of [...new Set([...tilesForShot(shot),...resident.map(r=>r.id)])]){
    // Read the exact endpoint actually used by this page; evidence includes both resident and served hashes.
    const manifest=await pages[side].evaluate(async id=>{const c=window.__videoCoarse;return await(await fetch(new URL(`${id}/manifest.json`,new URL(c.root,location.href)))).json();},tile);
    if((side===1&&(manifest.stale||(sourceRevision&&manifest.revision!==sourceRevision)))||manifest.pass_kind!=='full'){
     if(!preview)throw Error(`${name}: rejected ${tile}: stale=${manifest.stale} pass=${manifest.pass_kind}`);
    }
    if(side===1&&!preview&&!resident.some(r=>r.id===tile&&r.hash===manifest.visual?.[tile]?.sha256))throw Error(`${name}: resident hash mismatch ${tile}`);
    manifests.push({tile,revision:manifest.revision,stale:manifest.stale??false,pass:manifest.pass_kind,hash:manifest.visual?.[tile]?.sha256});
   }
   const count=preview?3:Math.round(config.duration*config.fps),motion=[];
   let encoder,closed;
   if(!preview){encoder=spawn('ffmpeg',['-hide_banner','-loglevel','error','-y','-f','image2pipe','-vcodec','mjpeg','-framerate',String(config.fps),'-i','pipe:0','-an','-c:v','libx264','-preset','fast','-crf','12','-pix_fmt','yuv420p','-threads','3',file],{stdio:['pipe','inherit','inherit']});closed=once(encoder,'close');encoder.stdin.on('error',()=>{});}
   for(let f=0;f<count;f++){
    const u=f/(count-1),state=await frame(side,shot,u,preview?Math.round(u*config.duration*config.fps):f);
    const png=await sessions[side].send('Page.captureScreenshot',{format:'jpeg',quality:97,captureBeyondViewport:false});
    const bytes=Buffer.from(png.data,'base64');
    if(preview||f===Math.floor(count/2))fs.writeFileSync(path.join(dir,`${name}-${preview?f:'sample'}.jpg`),bytes);
    if(encoder&&!encoder.stdin.write(bytes))await once(encoder.stdin,'drain');
    if(f%60===0||preview)motion.push({frame:f,...state});
   }
   if(encoder){encoder.stdin.end();if((await closed)[0])throw Error('ffmpeg capture failed');}
   fs.writeFileSync(receipt,JSON.stringify({shot,sourceRevision,url:urls[side],width,height,fps:config.fps,frames:count,manifests,resident,actors,motion,errors:failures[side],weather:side===0?'clear (production has no weather selector)':shot.weather,season:config.season},null,2));
   log(`captured ${name}: ${count} frames`);await sessions[side].send('Page.setWebLifecycleState',{state:'frozen'});
  }
 }
}finally{
 for(const p of pages)if(p)await p.close().catch(()=>{});
 if(browser)await browser.close();else{await ctx.close();lease('release');}
}
