// Capture actual deployed-service arrivals. Wall time is independent of fixed camera frame time.
import {chromeArgs,chromeIgnoreDefaultArgs,chromeExecutable} from '../dev/chrome-args.mjs';
import {chromium} from '../../game/node_modules/playwright-core/index.mjs';
import fs from 'node:fs';import path from 'node:path';import {spawn} from 'node:child_process';import {once} from 'node:events';
import {isolateBrowser,port,workRoot} from '../dev/environment.mjs';
isolateBrowser();const root=path.join(workRoot,'video');
const context=await chromium.launchPersistentContext(path.join(process.env.TURKU3D_BROWSER_DIR,'showcase-generation'),{
 executablePath:chromeExecutable(),ignoreDefaultArgs:chromeIgnoreDefaultArgs,headless:true,viewport:{width:1920,height:1080},deviceScaleFactor:1,
 args:chromeArgs(['--enable-unsafe-webgpu','--use-angle=metal','--ignore-gpu-blocklist','--disable-background-timer-throttling'])});
await context.addInitScript(()=>Object.defineProperty(navigator,'getGamepads',{value:()=>[]}));
const page=await context.newPage();
let begin=Date.now(); let enabled=false; const events=[],states={};
await page.route('**/*',async r=>{
 const u=new URL(r.request().url());
 if(u.hostname!=='127.0.0.1'||u.port!==String(port(1)))return r.abort();
 if(u.pathname.startsWith('/coarse/t0_1_0/')){
  if(!enabled&&/\/(collision|manifest)\.json$/.test(u.pathname))return r.fulfill({status:202,contentType:'application/json',body:'{"state":"building"}'});
  try{const response=await r.fetch({url:`http://127.0.0.1:${port(7)}${u.pathname}${u.search}`,timeout:120000});return r.fulfill({response});}catch{return r.abort();}
 }
 return r.continue();
});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
page.on('response',async r=>{const m=new URL(r.url()).pathname.match(/\/coarse\/(t0_-?\d+_-?\d+)\/(collision|manifest)\.json$/);if(!m)return;try{const b=await r.json();const e={elapsed:(Date.now()-begin)/1000,tile:m[1],status:r.status(),pass:b.pass_kind??null,state:b.state??null};events.push(e);states[m[1]]={...e,pass:e.pass??states[m[1]]?.pass??null};fs.appendFileSync(path.join(root,'evidence/generation-isolated-cityhall-events.jsonl'),JSON.stringify(e)+'\n');}catch{}});
try {
 await page.goto(`http://127.0.0.1:${port(1)}/?backend=webgpu&quality=high&time=2027-07-01T16:00:00%2B03:00&cam=23459860,6704190,110,0,-40,55`);
 await page.waitForFunction(()=>window.__qaMove,null,{timeout:30000});
 await page.waitForFunction(()=>window.__game.tiles.residentModels>=6,null,{timeout:90000});
 begin=Date.now();await fetch(`http://127.0.0.1:${port(7)}/coarse/t0_1_0/collision.json`);enabled=true;
 await page.evaluate(async()=>{
  const w=window;w.__cinema=await import('/assets/video.js');w.__pose=w.__cinema.curves([[360,110,310],[380,115,300],[400,120,290]],[[350,5,135],[360,5,125],[370,5,115]]);
  w.__videoTime=performance.now();performance.now=()=>w.__videoTime;w.__permit=false;
  w.__render.beforeFrame=()=>w.__permit;w.__render.afterFrame=()=>{w.__permit=false;w.__done=true;};
  const el=document.createElement('div');el.id='arrival-grid';el.style.cssText='position:fixed;right:48px;top:48px;width:480px;padding:24px;background:#061019ed;color:#f3f6f8;font:24px Arial;z-index:99999;border:1px solid #75e7cf;border-radius:12px';document.body.append(el);
 });
 const encoder=spawn('ffmpeg',['-v','error','-y','-f','image2pipe','-vcodec','mjpeg','-r','30','-i','pipe:0','-an','-c:v','libx264','-preset','fast','-crf','17','-pix_fmt','yuv420p','-profile:v','high','-threads','3',path.join(root,'shots/generation.mp4')],{stdio:['pipe','inherit','inherit']});
 const motion=[];let frames=1500, completedAt=null, actualFrames=0;
 for(let f=0;f<frames;f++){
  await new Promise(resolve=>setTimeout(resolve,100));
  const elapsed=(Date.now()-begin)/1000;
  const pose=await page.evaluate(({u,elapsed,states})=>{const w=window,p=w.__pose(u);w.__qaMove(p);w.__videoTime+=1000/30;w.__done=false;w.__permit=true;
   const cells=[];for(let j=-1;j<=1;j++)for(let i=0;i<=2;i++){const id=`t0_${i}_${j}`,s=states[id];const kind=s?.pass==='full'?'TEXTURED':s?.pass==='first'?'BASIC':s?.status===202?'BUILDING':'WAITING';cells.push(`<div style="padding:12px 5px;background:${kind==='TEXTURED'?'#224d49':kind==='BASIC'?'#56503a':'#162837'};border:1px solid #41616c;text-align:center"><b style="font-size:19px">${id}</b><br><span style="font-size:16px">${kind}</span></div>`);}
   document.getElementById('arrival-grid').innerHTML=`<b style="color:#75e7cf">ISOLATED OCI BUILD</b><p style="font-size:36px;margin:16px 0">${elapsed.toFixed(1)} s elapsed</p><div style="display:grid;grid-template-columns:repeat(3,1fr);gap:5px">${cells.join('')}</div><p style="font-size:18px;margin-bottom:0">Same deployed source · reused stage caches<br>Actual first/full build · time-lapse</p>`;return p;
  },{u:f/(frames-1),elapsed,states});
  await page.waitForFunction(()=>window.__done,null,{polling:'raf'});
  const bytes=await page.screenshot({type:'jpeg',quality:95});if(!encoder.stdin.write(bytes))await once(encoder.stdin,'drain');
  actualFrames=f+1;if(states.t0_1_0?.pass==='full')completedAt??=f;if(completedAt!==null&&f>Math.max(600,completedAt+90))break;
  if(f%30===0){motion.push({frame:f,elapsed,pose,states:structuredClone(states)});console.log('generation',f,elapsed.toFixed(1),Object.entries(states).filter(([id,s])=>s.pass).map(([id,s])=>id+':'+s.pass).join(' '));}
 }
 encoder.stdin.end();const [code]=await once(encoder,'exit');if(code)throw Error(`encoder ${code}`);
 fs.writeFileSync(path.join(root,'shots/generation.json'),JSON.stringify({shot:{id:'generation',seconds:actualFrames/30,realService:true,isolated:true,cacheReuse:true},info:{info:{backend:'WebGPU'}},wallSeconds:(Date.now()-begin)/1000,motion,events,errors},null,2));
} finally{await context.close();}
