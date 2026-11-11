#!/usr/bin/env node
// Trailer capture: deterministic free-camera clips and scout stills from the game's QA camera mode.
// Runs against an already leased browser (BROWSER_CDP from host-lease/session.py).
//   session.py run mac trailer --kind func --window-size 1920,1080 -- node tools/video/trailer-capture.mjs \
//     --shots tools/video/trailer-scout.json --still --out ~/turku3d/video/2026-11-11-trailer/scout
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {spawn,spawnSync} from 'node:child_process';
import {once} from 'node:events';
import {chromium} from '../../game/node_modules/playwright-core/index.mjs';
import {poseAt,hourAt,tilesAlong} from './trailer-paths.mjs';

const args=process.argv.slice(2),opt=(k,d)=>args.includes(k)?args[args.indexOf(k)+1]:d,flag=k=>args.includes(k);
const config=JSON.parse(fs.readFileSync(opt('--shots')));
const out=path.resolve(opt('--out',path.join(os.homedir(),'turku3d/video/2026-11-11-trailer/clips')));
const base=opt('--base','http://127.0.0.1:5970/'),still=flag('--still'),fps=Number(opt('--fps',config.fps??60));
const scale=Number(opt('--scale','1')),sub=Number(opt('--sub','1')),width=1920,height=1080;
const quality=opt('--quality','ultra'),leaseName=opt('--lease',process.env.TRAILER_LEASE??'trailer');
const selected=opt('--only','').split(',').filter(Boolean),version=opt('--version','');
const handles=Number(opt('--handles',config.handles??1));
const cdp=process.env.BROWSER_CDP;if(!cdp)throw Error('BROWSER_CDP is not set: run through host-lease/session.py');
const log=m=>console.log(new Date().toISOString().slice(11,19),m);
const touch=()=>spawnSync('python3',[path.join(os.homedir(),'turku3d/tools/host-lease/lease.py'),'touch',leaseName],{encoding:'utf8'}).status;
const iso=(shot,hour)=>{const h=((hour%24)+24)%24,total=Math.round(h*3600),p=n=>String(n).padStart(2,'0');
 return `${shot.date??config.date??'2026-09-08'}T${p(Math.floor(total/3600))}:${p(Math.floor(total%3600/60))}:${p(total%60)}+03:00`;};
const browser=await chromium.connectOverCDP(cdp),ctx=browser.contexts()[0];
await ctx.addInitScript(()=>Object.defineProperty(navigator,'getGamepads',{value:()=>[]}));
let page=null,session=null,loadedKey='';const failures=[],inflight=new Set();

async function open(shot){
 const key=`${shot.traffic??config.traffic??40}|${shot.walkers??config.walkers??100}|${shot.quality??quality}`;
 if(page&&key===loadedKey)return;
 const tag=`${key}|${scale}|${base}`;
 // A long-lived session keeps its loaded world: reuse the page that already runs this configuration.
 let reuse=null;
 if(!page)for(const p of ctx.pages())if(await p.evaluate(()=>window.__trailerKey).catch(()=>null)===tag)reuse=p;
 if(page&&!flag('--keep'))await page.close().catch(()=>{});
 page=reuse??await ctx.newPage();loadedKey=key;inflight.clear();
 session=await ctx.newCDPSession(page);
 await session.send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:scale,mobile:false});
 if(reuse){log(`reusing page ${tag}`);return;}
 page.on('pageerror',e=>failures.push(e.message));
 page.on('console',m=>{if(m.type()==='error')failures.push(m.text().slice(0,300));});
 page.on('request',r=>{if(/\.(glb|ktx2|col|png|jpg|webp|bin)(\?|$)/.test(r.url()))inflight.add(r);});
 page.on('requestfinished',r=>inflight.delete(r));page.on('requestfailed',r=>inflight.delete(r));
 const url=new URL(base),first=poseAt(shot,0),[traffic,walkers,q]=key.split('|');
 Object.entries({cam:[first.E,first.N,first.H,first.yaw,first.pitch,first.fov].join(','),time:iso(shot,hourAt(shot,0)),hud:0,quality:q,motion:1,traffic,walkers,...(config.params??{}),...(shot.params??{})})
  .forEach(([k,v])=>url.searchParams.set(k,v));
 log(`loading ${url}`);await page.goto(url.href,{waitUntil:'domcontentloaded',timeout:120000});
 await page.waitForFunction(()=>!!window.__qaMove&&!!window.__game?.replay&&!!window.__game?.coarse,null,{timeout:300000});
 await page.addStyleTag({content:'body > :not(canvas){display:none!important}html,body,canvas{cursor:none!important}'});
 await page.evaluate(tag=>{window.__trailerKey=tag;},tag);
}
// Free-running render until the view's tiles, scenery, vegetation and textures are in.
async function settle(shot,u,label){
 const pose=poseAt(shot,u),start=Date.now();let state,quiet=0;
 await page.evaluate(({pose,time,weather,wind})=>{
  const r=window.__render;r.beforeFrame=undefined;r.afterFrame=undefined;r.setTime(time);
  window.__dev?.setWeather?.(weather,0);
  if(wind!=null&&window.__dev?.weather?.sample)try{window.__dev.weather.sample.wind=wind;}catch{}
  window.__qaMove(pose);
 },{pose,time:iso(shot,hourAt(shot,u)),weather:shot.weather??'clear',wind:shot.wind});
 while(Date.now()-start<(shot.settleTimeout??240)*1000){
  state=await page.evaluate(()=>{const g=window.__game,c=g.coarse,cam=window.__render.camera.position,v=c.viewStatus(cam.x,cam.z);
   return{ready:window.__qaReady,error:window.__qaError,view:v.ready,near:v.near.length,notReady:v.near.filter(e=>!e.ready).map(e=>e.id),
    scenery:v.scenery.ready,vegetation:g.vegetation?.pending?.size??0,graphics:window.__render.graphics?.()};});
  if(state.error?.startsWith('timeout'))await page.evaluate(pose=>window.__qaMove(pose),pose);
  else if(state.error)throw Error(`${shot.id}: ${state.error}`);
  const idle=state.ready&&!state.notReady.length&&(state.scenery||(still&&!shot.scenery)||shot.allowPartial)&&!state.vegetation&&!inflight.size;
  quiet=idle?quiet+1:0;if(quiet>=2)break;
  await page.waitForTimeout(700);
 }
 if(quiet<2){if(!flag('--lenient'))throw Error(`settle timeout ${shot.id}@${u}: ${JSON.stringify(state)} inflight=${inflight.size}`);log(`WARN ${shot.id}@${u} not settled: ${JSON.stringify(state).slice(0,300)}`);}
 log(`settled ${label??shot.id}@${u} in ${((Date.now()-start)/1000).toFixed(1)}s`);
 await page.evaluate(()=>{window.__render.beforeFrame=()=>false;});
 return state;
}
// One exact frame at shot time t (seconds from clip start, may be negative during warm-up).
async function frame(shot,u,seconds,hold){
 const pose=poseAt(shot,u),time=shot.hourTo==null?null:iso(shot,hourAt(shot,u));
 return page.evaluate(({pose,seconds,time,hold})=>new Promise((resolve,reject)=>{
  const r=window.__render,g=window.__game;
  if(time)r.setTime(time);
  window.__dev?.rainEffects?.setPhaseSeconds?.(seconds);
  window.__qaMove(pose);
  const timer=setTimeout(()=>reject(Error('frame timeout')),60000);
  r.beforeFrame=()=>{g.replay.seek(seconds);return true;};
  r.afterFrame=()=>{r.beforeFrame=()=>false;r.afterFrame=undefined;clearTimeout(timer);
   const c=r.camera.position,v=hold?null:g.coarse.viewStatus(c.x,c.z);resolve({sim:g.clock.simSeconds,ready:v?.ready??true});};
 }),{pose,seconds,time,hold});
}
const shot1=async()=>Buffer.from((await session.send('Page.captureScreenshot',{format:'jpeg',quality:still?95:98,captureBeyondViewport:false})).data,'base64');
const lead=Number(opt('--lead','1.2'));// seconds of simulated pre-roll: temporal history, traffic and water settle
async function warm(shot,u0,t0){
 await page.evaluate(async({epochMs})=>{await window.__game.replay.reset({epochMs,seed:20261111});},{epochMs:Date.parse(iso(shot,hourAt(shot,u0)))});
 const n=Math.round(lead*fps);for(let k=0;k<n;k++)await frame(shot,u0,t0+(k+1)/fps,true);
 return t0+lead;
}
try{
 const shots=config.shots.filter(s=>!selected.length||selected.includes(s.id));
 for(const shot of shots){
  const started=Date.now();await open(shot);
  if(touch()===4)throw Error('lease lost');
  if(still){
   // Scout: one frame per requested hour (and per requested position along the path).
   const hours=shot.hours??[shot.hour??12],ats=shot.at??[0];
   for(const u of ats){
    for(const [k,hour] of hours.entries()){
     const s={...shot,hour,hourTo:undefined};
     await settle(s,u,`${shot.id} h${hour}`);
     if(shot.agl!=null&&k===0){
      // Eye height above the streamed ground under the camera (the keys' H is then only a first guess).
      const ground=await page.evaluate(()=>{const c=window.__render.camera.position;return window.__game.coarse.groundBelow(c.x,c.z);});
      if(Number.isFinite(ground)){for(const key of s.keys)key[2]=ground+shot.agl;shot.keys=s.keys;shot.ground=ground;await settle(s,u,`${shot.id} agl`);}
      else log(`WARN ${shot.id}: no ground under camera`);
     }
     const base=shot.preroll??20;await warm(s,u,base);
     const name=`${shot.id}${ats.length>1?`-u${u}`:''}${hours.length>1?`-h${String(hour).replace('.','_')}`:''}`;
     fs.mkdirSync(out,{recursive:true});fs.writeFileSync(path.join(out,`${name}.jpg`),await shot1());log(`still ${name}`);
    }
   }
   continue;
  }
  const dir=path.join(out,shot.id);fs.mkdirSync(dir,{recursive:true});
  let v=version;if(!v){let n=1;while(fs.existsSync(path.join(dir,`v${n}.mp4`)))n++;v=`v${n}`;}
  const file=path.join(dir,`${v}.mp4`),duration=(shot.duration??4)+2*handles,count=Math.round(duration*fps);
  // Visit the end and the middle first so their tiles are resident, then return to the start.
  for(const u of [1,.5,0])await settle(shot,u);
  const t0=await warm(shot,0,shot.preroll??20);
  const vf=scale>1?['-vf',`scale=${width}:${height}:flags=lanczos`]:[];
  const encoder=spawn('ffmpeg',['-hide_banner','-loglevel','error','-y','-f','image2pipe','-vcodec','mjpeg','-framerate',String(fps),'-i','pipe:0','-an',...vf,
   '-c:v','libx264','-preset','slow','-crf',String(opt('--crf','10')),'-pix_fmt','yuv420p','-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-movflags','+faststart',file],{stdio:['pipe','inherit','inherit']});
  const closed=once(encoder,'close');encoder.stdin.on('error',()=>{});
  const receipts=[];let resettled=0;
  for(let f=0;f<count;f++){
   const u=count>1?f/(count-1):0,t=t0+f/fps;
   let bytes;
   if(sub>1){
    // Temporal supersampling: average sub-frames across the shutter interval (180 degrees by default).
    const shutter=shot.shutter??.5,parts=[];
    for(let k=0;k<sub;k++){const o=(k/(sub)-.5)*shutter;await frame(shot,Math.min(1,Math.max(0,(f+o)/(count-1))),t+o/fps,true);parts.push(await shot1());}
    bytes=await page.evaluate(async({parts,w,h})=>{
     const c=new OffscreenCanvas(w,h),x=c.getContext('2d');
     for(const [i,b64] of parts.entries()){const img=await createImageBitmap(await(await fetch('data:image/jpeg;base64,'+b64)).blob());x.globalAlpha=1/(i+1);x.drawImage(img,0,0,w,h);img.close();}
     const blob=await c.convertToBlob({type:'image/jpeg',quality:.98});const a=new Uint8Array(await blob.arrayBuffer());let s='';for(let i=0;i<a.length;i+=32768)s+=String.fromCharCode(...a.subarray(i,i+32768));return btoa(s);
    },{parts:parts.map(p=>p.toString('base64')),w:width*scale,h:height*scale}).then(s=>Buffer.from(s,'base64'));
   }else{
    let state=await frame(shot,u,t,false);
    if(!state.ready&&!shot.allowPartial){resettled++;await settle(shot,u);state=await frame(shot,u,t,false);}
    bytes=await shot1();
    if(f%60===0)receipts.push({frame:f,u,...state});
   }
   if(f===Math.floor(count/2))fs.writeFileSync(path.join(dir,`${v}-mid.jpg`),bytes);
   if(!encoder.stdin.write(bytes))await once(encoder.stdin,'drain');
   if(f%120===0){log(`${shot.id} ${v} frame ${f}/${count}`);if(touch()===4)throw Error('lease lost');}
  }
  encoder.stdin.end();if((await closed)[0])throw Error('ffmpeg failed');
  const wall=(Date.now()-started)/1000;
  fs.writeFileSync(path.join(dir,`${v}.json`),JSON.stringify({shot,file,fps,frames:count,handles,scale,sub,quality,base,wallSeconds:wall,resettled,receipts,errors:failures.splice(0)},null,1));
  // Contact sheet: 8 frames across the clip.
  spawnSync('ffmpeg',['-hide_banner','-loglevel','error','-y','-i',file,'-vf',`select='not(mod(n\\,${Math.max(1,Math.floor(count/8))}))',scale=640:-1,tile=4x2`,'-frames:v','1','-q:v','3',path.join(dir,`${v}-contact.jpg`)]);
  log(`captured ${shot.id} ${v}: ${count} frames in ${wall.toFixed(0)}s (${(wall/duration).toFixed(1)} s/s), resettled ${resettled}`);
 }
}finally{
 if(page&&flag('--close'))await page.close().catch(()=>{});
 else if(page)await page.evaluate(()=>{window.__render.beforeFrame=undefined;window.__render.afterFrame=undefined;}).catch(()=>{});
 if(still)fs.writeFileSync(path.join(out,`${path.basename(opt('--shots'),'.json')}-resolved.json`),JSON.stringify(config,null,1));
 await browser.close().catch(()=>{});
}
