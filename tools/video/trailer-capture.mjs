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
import {createHash} from 'node:crypto';
import {chromium} from '../../game/node_modules/playwright-core/index.mjs';
import {poseAt as pathPose,hourAt} from './trailer-paths.mjs';
import {carAt,anchorAt} from './trailer-cars.mjs';
// Clip seconds for a path fraction (stills: the requested moment `shot.carTime`).
let clipSeconds=()=>0;
const poseAt=(shot,u,seconds=clipSeconds(shot,u))=>pathPose(shot,u,shot.follow&&shot.cars?anchorAt(shot.cars[shot.follow.car??0],seconds,shot.follow):undefined);
const carsAt=(shot,seconds)=>(shot.cars??[]).map(car=>carAt(car,seconds));
// A scripted walk: {from:[E,N],heading,speed,t0} in engine metres for the page.
const walkAt=(shot,seconds)=>{const w=shot.walker;if(!w)return null;const h=w.heading*Math.PI/180,v=w.speed??1.4,d=v*((w.t0??0)+seconds);
 return{x:w.from[0]-23459500+Math.sin(h)*d,z:6704500-w.from[1]-Math.cos(h)*d,vx:Math.sin(h)*v,vz:-Math.cos(h)*v,y:w.H};};

const args=process.argv.slice(2),opt=(k,d)=>args.includes(k)?args[args.indexOf(k)+1]:d,flag=k=>args.includes(k);
const config=JSON.parse(fs.readFileSync(opt('--shots')));
const out=path.resolve(opt('--out',path.join(os.homedir(),'turku3d/video/2026-11-11-trailer/clips')));
const base=opt('--base','http://127.0.0.1:5970/'),still=flag('--still'),fps=Number(opt('--fps',config.fps??60));
// Two tiers with identical paths, actors, timing and frame counts: `draft` (fast, judgeable: high preset, 1280x720,
// near-tile readiness only, dN.mp4) and `final` (ultra, 1920x1080, whole visible world asserted, vN.mp4).
const tier=opt('--tier','final'),draft=tier==='draft';if(!['draft','final'].includes(tier))throw Error('--tier draft|final');
const scale=Number(opt('--scale','1')),sub=Number(opt('--sub','1')),[width,height]=opt('--size',draft&&!still?'1280x720':'1920x1080').split('x').map(Number);
const quality=opt('--quality',draft?'high':'ultra'),leaseName=opt('--lease',process.env.TRAILER_LEASE??'trailer');
const selected=opt('--only','').split(',').filter(Boolean),version=opt('--version','');
const handles=Number(opt('--handles',config.handles??1));
const cdp=process.env.BROWSER_CDP;if(!cdp)throw Error('BROWSER_CDP is not set: run through host-lease/session.py');
const log=m=>console.log(new Date().toISOString().slice(11,19),m);
const touch=()=>leaseName==='none'?0:spawnSync('python3',[path.join(os.homedir(),'turku3d/tools/host-lease/lease.py'),'touch',leaseName],{encoding:'utf8'}).status;
const iso=(shot,hour)=>{const h=((hour%24)+24)%24,total=Math.round(h*3600),p=n=>String(n).padStart(2,'0');
 return `${shot.date??config.date??'2026-09-08'}T${p(Math.floor(total/3600))}:${p(Math.floor(total%3600/60))}:${p(total%60)}+03:00`;};
const browser=await chromium.connectOverCDP(cdp),ctx=browser.contexts()[0];
await ctx.addInitScript(()=>Object.defineProperty(navigator,'getGamepads',{value:()=>[]}));
let page=null,session=null,loadedKey='';const failures=[],inflight=new Set();

async function open(shot){
 // One page per configuration: the URL reserves the ambient population, each shot then sets its own traffic cap.
 const key=`${opt('--traffic',config.traffic??40)}|${opt('--walkers',config.walkers??100)}|${quality}`;
 if(page&&key===loadedKey)return;
 const tag=`${key}|${scale}|${base}`;
 // A long-lived session keeps its loaded world: reuse the page that already runs this configuration.
 let reuse=null;
 if(!page)for(const p of ctx.pages())if(await p.evaluate(()=>window.__trailerKey).catch(()=>null)===tag)reuse=p;
 if(page&&!flag('--keep'))await page.close().catch(()=>{});
 const url=new URL(base),first=poseAt(shot,0),[traffic,walkers,q]=key.split('|');
 Object.entries({cam:[first.E,first.N,first.H,first.yaw,first.pitch,first.fov].join(','),time:iso(shot,hourAt(shot,0)),hud:0,quality:q,motion:1,traffic,walkers,acceptStale:1,...(config.params??{}),...(shot.params??{})})
  .forEach(([k,v])=>url.searchParams.set(k,v));
 // An interrupted run may have left this configuration loading: adopt that page instead of loading twice.
 const adopt=reuse?null:ctx.pages().find(p=>new URL(p.url()).search.includes(`quality=${q}`)&&new URL(p.url()).search.includes(`traffic=${traffic}&walkers=${walkers}`)&&p.url().startsWith(base));
 page=reuse??adopt??await ctx.newPage();loadedKey=key;inflight.clear();
 session=await ctx.newCDPSession(page);
 await session.send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:scale,mobile:false});
 if(reuse){log(`reusing page ${tag}`);return;}
 page.on('pageerror',e=>failures.push(e.message));
 page.on('console',m=>{if(m.type()==='error')failures.push(m.text().slice(0,300));});
 page.on('request',r=>{if(/\.(glb|ktx2|col|png|jpg|webp|bin)(\?|$)/.test(r.url()))inflight.add(r);});
 page.on('requestfinished',r=>inflight.delete(r));page.on('requestfailed',r=>inflight.delete(r));
 if(adopt)log(`adopting loading page ${adopt.url().slice(0,90)}`);
 else{log(`loading ${url}`);await page.goto(url.href,{waitUntil:'domcontentloaded',timeout:120000});}
 await page.waitForFunction(()=>!!window.__qaMove&&!!window.__game?.replay&&!!window.__game?.coarse,null,{timeout:300000});
 await page.addStyleTag({content:'body > :not(canvas){display:none!important}html,body,canvas{cursor:none!important}'});
 await page.evaluate(({tag,draft})=>{window.__trailerKey=tag;window.__trailerDraft=draft;},{tag,draft});
}
// Replay actors: the game's own fleet models and tyre effects, driven by recorded physics states.
async function installCars(shot){
 await page.evaluate(async({cars,quality,walker})=>{
  const T=window.__trailerCars??={visuals:[],tyres:null,key:''},key=JSON.stringify([cars.map(c=>[c.model,c.color]),!!walker]);
  if(T.key===key)return;
  const scene=window.__render.environment.scene;
  for(const v of T.visuals)v.root.removeFromParent();T.visuals=[];T.key=key;
  if(!cars.length&&!walker){T.apply=null;return;}
  const {CarVisual}=await import('/src/actors/car.ts');
  // Trailer paint: a saturated yellow for the hero car (the game's palette has no light saturated colour).
  const paint=await import('/src/actors/parkedPaint.ts');if(!paint.PAINT_COLORS.yellow)paint.PAINT_COLORS.yellow=[238,186,22];
  for(const c of cars){const v=new CarVisual(scene,c.model??'golf7',c.color??'red');await v.load();T.visuals.push(v);}
  if(!T.tyres){const {TyreEffects}=await import('/src/vehicle/tyreEffects.ts');T.tyres=new TyreEffects(scene,quality);}
  if(walker&&!T.walker){
   // The game's own player character on a scripted straight walk (as in the 2 November film).
   const source=await(await fetch('/src/actors/player.ts')).text(),threeURL=source.match(/import \* as THREE from ["']([^"']+)["']/)?.[1];
   T.THREE=await import(threeURL);const {PlayerVisual}=await import('/src/actors/player.ts');
   const actor=new PlayerVisual(scene,async()=>null);await actor.load('/assets/character/player.glb',window.__render.renderer);T.walker=actor;
  }
  T.walk=null;
  T.apply=(states,dt,walk)=>{
   if(T.walker){T.walker.root.visible=!!walk;if(walk){const THREE=T.THREE,g=window.__game.coarse,y=g.groundBelow(walk.x,walk.z);
    T.walker.update(dt||1/60,new THREE.Vector3(walk.x,walk.y??(Number.isFinite(y)?y:0),walk.z),new THREE.Vector3(walk.vx,0,walk.vz),true,0);}}
   const g=window.__game.coarse;
   T.tyres.begin(dt);
   states.forEach((s,i)=>{
    if(s.moved){const a=g.groundBelow(s.moved.from.x,s.moved.from.z),b=g.groundBelow(s.pos.x,s.pos.z),d=Number.isFinite(a)&&Number.isFinite(b)?b-a:0;
     s.pos.y+=d;}
    T.visuals[i].update(s);T.tyres.vehicle(i+1,s.pos,s.rot,s.wheels,'car',dt,s.speed??0);});
   T.tyres.end(window.__render.camera);
  };
 },{cars:shot.cars??[],quality:shot.quality??quality,walker:!!shot.walker});
 if(shot.cars?.length||shot.walker){await page.evaluate(({states,walk})=>window.__trailerCars.apply(states,0,walk),{states:carsAt(shot,0),walk:walkAt(shot,0)});await page.waitForTimeout(2500);}
}
// The static OCI host marks some full products stale and never rebuilds them: accept them, and report them.
async function acceptStale(){
 // The static host has no product for some cells at the edge of the generated world (`missing`): they can never
 // load, so the rule is that none of them lies inside the camera frustum (nothing missing in frame).
 await page.evaluate(async()=>{
  const {sceneryBounds}=await import('/src/world/sceneryView.ts');
  window.__trailerScenery=()=>{
   const s=window.__game.scenery,p=s?.progress;if(!p)return{ok:true,missing:0,inFrame:0};
   const cam=window.__render.camera;cam.updateMatrixWorld();const m=cam.projectionMatrix.clone().multiply(cam.matrixWorldInverse).elements;
   const outside=id=>{const b=sceneryBounds(id),c=[];
    for(const x of [b.x,b.x+b.size])for(const z of [b.z,b.z+b.size])for(const y of [-30,160])c.push([m[0]*x+m[4]*y+m[8]*z+m[12],m[1]*x+m[5]*y+m[9]*z+m[13],m[3]*x+m[7]*y+m[11]*z+m[15]]);
    return c.every(q=>q[2]<=0)||c.every(q=>q[0]<-q[2])||c.every(q=>q[0]>q[2])||c.every(q=>q[1]<-q[2])||c.every(q=>q[1]>q[2]);};
   const missing=s.footprint.filter(id=>s.unavailable.has(id)&&!s.detailed(id)),inFrame=missing.filter(id=>!outside(id));
   const committed=s.wanted.every(id=>s.entries.get(id)?.model?.visible);
   return{ok:p.ready||(committed&&!p.batching&&!p.partial&&!p.pending&&!p.errors&&!p.cataloguePending&&p.loaded===p.total&&(!inFrame.length||!!window.__trailerAllowMissing)),missing:missing.length,inFrame:inFrame.length};
  };
 });
 await page.evaluate(()=>{
  const s=window.__game.scenery;if(!s||s.__acceptStale||new URLSearchParams(location.search).has('acceptStale'))return;
  const request=s.request.bind(s);s.__acceptStale=true;
  s.request=async url=>{const r=await request(url);if(!r.ok||!/scenery\/[0-9a-f]+\.json/.test(url))return r;
   const j=await r.json();j.stale=false;return new Response(JSON.stringify(j),{status:200,headers:{'content-type':'application/json'}});};
 });
}
// Free-running render until the view's tiles, scenery, vegetation and textures are in.
async function settle(shot,u,label){
 await acceptStale();
 const pose=poseAt(shot,u),start=Date.now();let state,quiet=0;
 await page.evaluate(({pose,time,weather,wind,radius,allowMissing})=>{
  window.__trailerAllowMissing=allowMissing;const r=window.__render;r.beforeFrame=undefined;r.afterFrame=undefined;r.setTime(time);
  window.__dev?.setWeather?.(weather,0);
  if(radius&&window.__game.scenery&&window.__game.scenery.radius!==radius)window.__game.scenery.setRadius(radius);
  if(wind!=null&&window.__dev?.weather?.sample)try{window.__dev.weather.sample.wind=wind;}catch{}
  window.__qaMove(pose);
 },{pose,time:iso(shot,hourAt(shot,u)),weather:shot.weather??'clear',wind:shot.wind,radius:shot.sceneryRadius??(draft?null:8000),allowMissing:!!shot.allowMissing});
 while(Date.now()-start<(shot.settleTimeout??(draft?150:900))*1000){
  state=await page.evaluate(()=>{const g=window.__game,c=g.coarse,cam=window.__render.camera.position,v=c.viewStatus(cam.x,cam.z);
   const gr=window.__render.graphics?.(),st=window.__perf?.stream,sc=v.scenery;
   const full=id=>{const e=c.entries.get(id);return e?.state==='none'||(!!e?.model&&!e.preview&&e.pass==='full');};
   return{ready:window.__qaReady,error:window.__qaError,view:v.ready,near:v.near.length,notReady:v.near.filter(e=>!full(e.id)).map(e=>e.id),
    staleNear:v.near.filter(e=>c.entries.get(e.id)?.stale).map(e=>e.id),
    scenery:window.__trailerScenery().ok,missing:window.__trailerScenery().missing,missingInFrame:window.__trailerScenery().inFrame,sceneryLoaded:`${sc.loaded}/${sc.total}`,vegetation:g.vegetation?.pending?.size??0,
    queue:(gr?.waiting??[]).reduce((a,b)=>a+b,0)+(gr?.actors?.waiting??[]).reduce((a,b)=>a+b,0)+(gr?.active?1:0)+(gr?.actors?.active?1:0),
    stream:(st?.pending??0)+(st?.installing??0)+(st?.previews??0)};});
  if(state.error?.startsWith('timeout'))await page.evaluate(pose=>window.__qaMove(pose),pose);
  else if(state.error)throw Error(`${shot.id}: ${state.error}`);
  // Jonni 2026-11-11: the whole visible world resident before any frame: near tiles full, scenery complete,
  // vegetation placed, the graphics (program build) queues and the tile stream idle, no asset request in flight.
  const idle=draft?(shot.allowPartial||shot.draftPartial||(state.ready&&!state.notReady.length))&&!state.vegetation&&!inflight.size&&(shot.scenery?state.scenery||state.sceneryLoaded.split('/')[0]===state.sceneryLoaded.split('/')[1]:true)
   :state.ready&&!state.notReady.length&&state.scenery&&!state.vegetation&&!state.queue&&!state.stream&&!inflight.size;
  quiet=idle?quiet+1:0;if(quiet>=3)break;
  if((Date.now()-start)%20000<750)log(`  waiting ${shot.id}@${u}: ready ${state.ready} near ${state.near-state.notReady.length}/${state.near} scenery ${state.sceneryLoaded} missing ${state.missing} (in frame ${state.missingInFrame}) queue ${state.queue} stream ${state.stream} veg ${state.vegetation} inflight ${inflight.size}`);
  await page.waitForTimeout(700);
 }
 if(quiet<3){if(!flag('--lenient')&&!draft)throw Error(`settle timeout ${shot.id}@${u}: ${JSON.stringify(state)} inflight=${inflight.size}`);log(`WARN ${shot.id}@${u} not settled: ${JSON.stringify(state).slice(0,300)}`);}
 log(`settled ${label??shot.id}@${u} in ${((Date.now()-start)/1000).toFixed(1)}s`);
 await page.evaluate(()=>{window.__render.beforeFrame=()=>false;});
 return state;
}
// One exact frame at shot time t (seconds from clip start, may be negative during warm-up).
async function frame(shot,u,seconds,hold,clip=clipSeconds(shot,u)){
 const pose=poseAt(shot,u,clip),time=shot.hourTo==null&&!shot.hourKeys?null:iso(shot,hourAt(shot,u)),cars=shot.cars?.length||shot.walker?carsAt(shot,clip):null,walk=walkAt(shot,clip);
 return page.evaluate(({pose,seconds,time,hold,cars,dt,walk})=>new Promise((resolve,reject)=>{
  const r=window.__render,g=window.__game;
  if(time)r.setTime(time);
  if(cars)window.__trailerCars.apply(cars,dt,walk);
  window.__dev?.rainEffects?.setPhaseSeconds?.(seconds);
  window.__qaMove(pose);
  const timer=setTimeout(()=>reject(Error('frame timeout')),60000);
  r.beforeFrame=()=>{g.replay.seek(seconds);return true;};
  r.afterFrame=()=>{r.beforeFrame=()=>false;r.afterFrame=undefined;clearTimeout(timer);
   const c=r.camera.position,v=hold?null:g.coarse.viewStatus(c.x,c.z),k=g.coarse;
   const full=id=>{const e=k.entries.get(id);return e?.state==='none'||(!!e?.model&&!e.preview&&e.pass==='full');};
   // Camera height above the streamed ground, and above the wheels' ground of every replayed car nearby.
   let clear=c.y-k.groundBelow(c.x,c.z);if(!Number.isFinite(clear))clear=null;
   for(const s of cars??[])if(Math.hypot(s.pos.x-c.x,s.pos.z-c.z)<30){const d=c.y-(s.pos.y-.62);clear=clear==null?d:Math.min(clear,d);}
   resolve({clear,sim:g.clock.simSeconds,ready:v?v.near.every(e=>full(e.id))&&(window.__trailerScenery().ok||!!window.__trailerDraft):true,scenery:v?`${v.scenery.loaded}/${v.scenery.total}`:null,missingInFrame:v?window.__trailerScenery().inFrame:0,missing:v?window.__trailerScenery().missing:0});};
 }),{pose,seconds,time,hold,cars,walk,dt:1/fps/Math.max(1,sub)});
}
const shot1=async()=>Buffer.from((await session.send('Page.captureScreenshot',{format:'jpeg',quality:still?95:draft?90:98,captureBeyondViewport:false})).data,'base64');
const lead=Number(opt('--lead','1.2'));// seconds of simulated pre-roll: temporal history, traffic and water settle
async function warm(shot,u0,t0){
 await page.evaluate(async({epochMs,cap})=>{
  const p=window.__game.population;if(p){p.__reserved??=p.cap;p.cap=Math.min(p.__reserved,cap??p.__reserved);}
  await window.__game.replay.reset({epochMs,seed:20261111});},{epochMs:Date.parse(iso(shot,hourAt(shot,u0))),cap:shot.traffic});
 const n=Math.round(lead*fps),c0=clipSeconds(shot,u0);for(let k=0;k<n;k++)await frame(shot,u0,t0+(k+1)/fps,true,c0-(n-1-k)/fps);
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
     const stillName=`${shot.id}${ats.length>1?`-u${u}`:''}${hours.length>1?`-h${String(hour).replace('.','_')}`:''}`;
     if(fs.existsSync(path.join(out,`${stillName}.jpg`))&&!flag('--force')){log(`skip ${stillName}`);continue;}
     clipSeconds=()=>shot.carTime??0;await installCars(s);
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
  const prefix=draft?'d':'v';
  let v=version;if(!v){let n=1;while(fs.existsSync(path.join(dir,`${prefix}${n}.mp4`)))n++;v=`${prefix}${n}`;}
  const file=path.join(dir,`${v}.mp4`),duration=(shot.duration??4)+2*handles,count=Math.round(duration*fps);
  clipSeconds=(_shot,u)=>u*duration;await installCars(shot);
  // Visit the end and the middle first so their tiles are resident, then return to the start.
  for(const u of shot.stations??(draft?[1,0]:[1,.75,.5,.25,0]))await settle(shot,u);
  const t0=await warm(shot,0,shot.preroll??20);
  const readyWall=(Date.now()-started)/1000,framesStarted=Date.now();
  const vf=scale>1?['-vf',`scale=${width}:${height}:flags=lanczos`]:[];
  const encoder=spawn('ffmpeg',['-hide_banner','-loglevel','error','-y','-f','image2pipe','-vcodec','mjpeg','-framerate',String(fps),'-i','pipe:0','-an',...vf,
   '-c:v','libx264','-preset',draft?'veryfast':'slow','-crf',String(opt('--crf',draft?'18':'10')),'-pix_fmt','yuv420p','-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-movflags','+faststart',file],{stdio:['pipe','inherit','inherit']});
  const closed=once(encoder,'close');encoder.stdin.on('error',()=>{});
  const receipts=[];let resettled=0,duplicates=0,lastHash='',minClear=Infinity,maxMissing=0,maxInFrame=0;
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
    if(!state.ready&&!shot.allowPartial&&!draft){resettled++;await settle(shot,u);state=await frame(shot,u,t,false);}
    bytes=await shot1();
    if(state.clear!=null)minClear=Math.min(minClear,state.clear);
    // Never ship a frame from under the ground (Jonni 2026-11-11).
    if(state.clear!=null&&state.clear<(shot.minClear??.2)){const m=`${shot.id}: camera ${state.clear.toFixed(2)} m above ground at frame ${f}`;if(!draft)throw Error(m);if(f%30===0)log(`WARN ${m}`);}
    if(f%60===0)receipts.push({frame:f,u,...state});
    maxMissing=Math.max(maxMissing,state.missing??0);maxInFrame=Math.max(maxInFrame,state.missingInFrame??0);
   }
   const hash=createHash('md5').update(bytes).digest('hex');if(hash===lastHash)duplicates++;lastHash=hash;
   if(f===Math.floor(count/2))fs.writeFileSync(path.join(dir,`${v}-mid.jpg`),bytes);
   if(!encoder.stdin.write(bytes))await once(encoder.stdin,'drain');
   if(f%120===0){log(`${shot.id} ${v} frame ${f}/${count}`);if(touch()===4)throw Error('lease lost');}
  }
  encoder.stdin.end();if((await closed)[0])throw Error('ffmpeg failed');
  const wall=(Date.now()-started)/1000,frameWall=(Date.now()-framesStarted)/1000;
  // Frame cadence proof: decoded frame count, frames mpdecimate would drop as near-duplicates, and frozen spans.
  const probe=spawnSync('ffprobe',['-v','error','-count_frames','-select_streams','v:0','-show_entries','stream=nb_read_frames,r_frame_rate,width,height','-of','json',file],{encoding:'utf8'});
  const dec=spawnSync('ffmpeg',['-hide_banner','-i',file,'-vf','mpdecimate=hi=64*4:lo=64*2:frac=0.1,showinfo','-f','null','-'],{encoding:'utf8',maxBuffer:1<<28});
  const kept=(dec.stderr.match(/Parsed_showinfo.*pts_time/g)??[]).length;
  const freeze=spawnSync('ffmpeg',['-hide_banner','-i',file,'-vf','freezedetect=n=-70dB:d=0.03','-f','null','-'],{encoding:'utf8',maxBuffer:1<<28});
  const cadence={stream:JSON.parse(probe.stdout||'{}').streams?.[0],expectedFrames:count,exactDuplicateCaptures:duplicates,mpdecimateDropped:count-kept,freezes:(freeze.stderr.match(/freeze_start/g)??[]).length};
  const pass=resettled===0&&duplicates===0&&cadence.freezes===0&&minClear>=(shot.minClear??.2)&&Number(cadence.stream?.nb_read_frames)===count;
  fs.writeFileSync(path.join(dir,`${v}.json`),JSON.stringify({tier,pass:draft?null:pass,size:`${width}x${height}`,shot,file,fps,frames:count,handles,scale,sub,preset:shot.quality??quality,overrides:{motion:1,hud:0,traffic:loadedKey.split('|')[0],walkers:loadedKey.split('|')[1],...(config.params??{}),...(shot.params??{})},base,wallSeconds:wall,readySeconds:readyWall,frameSeconds:frameWall,sceneryRadius:shot.sceneryRadius??(draft?'preset':8000),
   variant:shot.variant??null,unavailableCells:maxMissing,unavailableCellsInFrame:maxInFrame,minCameraClearance:Number.isFinite(minClear)?+minClear.toFixed(2):null,readiness:{rule:'qaReady, near tiles full/non-preview, far scenery complete (every wanted cell loaded, visible and full; cells the host marks stale are accepted; cells the host has no product for must lie outside the camera frustum), vegetation pending 0, graphics queues idle, stream idle, no asset request in flight; asserted at path stations before frame 0 and per captured frame (viewStatus.ready)',framesResettled:resettled},cadence,receipts,errors:failures.splice(0)},null,1));
  log(`${shot.id} ${v} checks: pass=${pass} minClear=${Number.isFinite(minClear)?minClear.toFixed(2):'n/a'} resettled=${resettled} exactDup=${duplicates} mpdecimateDropped=${cadence.mpdecimateDropped} freezes=${cadence.freezes} frames=${cadence.stream?.nb_read_frames}/${count}`);
  // Contact sheet: 8 frames across the clip.
  spawnSync('ffmpeg',['-hide_banner','-loglevel','error','-y','-i',file,'-vf',`select='not(mod(n\\,${Math.max(1,Math.floor(count/8))}))',scale=640:-1,tile=4x2`,'-frames:v','1','-q:v','3',path.join(dir,`${v}-contact.jpg`)]);
  log(`captured ${shot.id} ${v}: ${count} frames in ${wall.toFixed(0)}s total; frames only ${frameWall.toFixed(0)}s = ${(frameWall/duration).toFixed(1)} s per clip second; ready after ${readyWall.toFixed(0)}s`);
 }
}finally{
 if(page&&flag('--close'))await page.close().catch(()=>{});
 // Draft pages rest paused (no GPU load); the final page keeps streaming so the next shot's world warms up.
 else if(page)await page.evaluate(paused=>{window.__render.beforeFrame=paused?()=>false:undefined;window.__render.afterFrame=undefined;},draft&&!flag('--running')).catch(()=>{});
 if(still)fs.writeFileSync(path.join(out,`${path.basename(opt('--shots'),'.json')}-resolved.json`),JSON.stringify(config,null,1));
 await browser.close().catch(()=>{});
}
